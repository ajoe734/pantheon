"""Agora Operational and Data Readiness service and FastAPI router (SD-AGC-06).

Provides GET /bff/agora/operational-readiness to compose:
  - Source snapshot identity, source time, age, SLA (86400s), and freshness.
  - Source-instance desired and observed state.
  - Paper-signal-producer health, active binding, last success, and consumed snapshot.
  - Projection cursor/freshness for signals, decision_events, inbox, journal,
    candidates, interactions, and performance.
  - Exact BFF deployment identity.

Invariants:
  1. Operational readiness binds source snapshot and producer identity.
  2. Distinct states: 'fresh', 'stale', 'empty_fresh', 'unavailable', 'degraded', 'not_configured'.
  3. Stale, empty_fresh, and unavailable are distinct and mutually exclusive.
  4. The route is read-only and never auth-critical (requiredForAuthentication=False).
  5. Never called by /bff/auth/readiness.
  6. Carries no broker order authority or capital mutation ability.
"""
from __future__ import annotations

import os
import inspect
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Literal, Mapping, Optional, Protocol, Tuple

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from .identity.scope import AgoraScopeResolutionError, resolve_agora_user_scope
from .models import AgoraCapabilityScope
from services.execution.market_snapshot_admission import (
    evaluate_taiwan_market_freshness,
    is_taiwan_symbol,
    parse_rfc3339,
)


# --------------------------------------------------------------------------- #
# DTO Models
# --------------------------------------------------------------------------- #

FreshnessState = Literal[
    "fresh",
    "stale",
    "empty_fresh",
    "unavailable",
    "degraded",
    "not_configured",
]

ReadinessStatus = Literal[
    "ok",
    "degraded",
    "unavailable",
    "empty_fresh",
    "stale",
    "not_configured",
]


class AgoraSourceReadiness(BaseModel):
    """Source snapshot identity and freshness readback."""
    snapshot_id: Optional[str] = None
    source_instance_id: Optional[str] = None
    source_timestamp: Optional[str] = None
    age_seconds: Optional[float] = None
    sla_seconds: int = 86400
    freshness: FreshnessState = "unavailable"
    desired_state: Optional[str] = None
    observed_state: Optional[str] = None
    last_failure: Optional[Any] = None


class AgoraSignalProducerReadiness(BaseModel):
    """Paper signal producer health and consumed snapshot binding."""
    status: ReadinessStatus = "unavailable"
    producer_id: str = "paper-signal-producer"
    active_binding: Optional[str] = None
    consumed_snapshot_id: Optional[str] = None
    last_success_at: Optional[str] = None
    enqueued: int = 0
    reason: Optional[str] = None


class AgoraSurfaceReadiness(BaseModel):
    """Readiness status of an individual downstream Agora surface."""
    status: ReadinessStatus = "unavailable"
    count: int = 0
    reason: Optional[str] = None
    freshness: Optional[str] = None
    cursor: Optional[str] = None


class AgoraDeploymentReadiness(BaseModel):
    """Exact BFF deployment identity."""
    service: str = "pantheon-bff"
    environment: Optional[str] = "dev"
    source_commit_sha: Optional[str] = None
    bundle_version: Optional[str] = "v1.13"


class AgoraOperationalReadinessData(BaseModel):
    """Composed data payload for Agora operational readiness."""
    status: ReadinessStatus
    source: AgoraSourceReadiness
    signal_producer: AgoraSignalProducerReadiness
    surfaces: Dict[str, AgoraSurfaceReadiness]
    deployment: Optional[AgoraDeploymentReadiness] = None


class AgoraOperationalReadinessMeta(BaseModel):
    """Metadata attached to operational readiness response."""
    snapshot_at: str
    capability: str = "agora.operational_readiness.v1"
    requiredForAuthentication: bool = False
    no_order_route_proof: str = "agora_operational_readiness_read_only"
    audience: Optional[str] = None


class AgoraOperationalReadinessEnvelope(BaseModel):
    """Envelope wrapper for operational readiness."""
    data: AgoraOperationalReadinessData
    meta: AgoraOperationalReadinessMeta


# --------------------------------------------------------------------------- #
# Helper parsing functions
# --------------------------------------------------------------------------- #

def _parse_iso_utc(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _format_utc(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean_text(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _as_mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


@dataclass
class AgoraOperationalReadinessInputs:
    """One request-scoped, read-only authority snapshot.

    ``errors`` is intentionally component-scoped.  A failed producer read must
    not erase a successfully read Source snapshot (and vice versa), while every
    failure still degrades or fails closed with a typed reason.
    """

    source_snapshot: Optional[Dict[str, Any]] = None
    source_instance: Optional[Dict[str, Any]] = None
    signal_producer: Optional[Dict[str, Any]] = None
    surfaces: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    errors: Dict[str, str] = field(default_factory=dict)


class AgoraOperationalReadinessReadProvider(Protocol):
    """Typed read-only port used by the public readiness composition."""

    def read(
        self,
        *,
        scope: Optional[AgoraCapabilityScope],
        now_dt: datetime,
    ) -> AgoraOperationalReadinessInputs: ...


def _call_scoped_reader(
    reader: Callable[..., Any],
    scope: Optional[AgoraCapabilityScope],
) -> Any:
    """Call a read port with only the scope arguments it declares."""

    try:
        parameters = inspect.signature(reader).parameters
    except (TypeError, ValueError):
        parameters = {}
    accepts_kwargs = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    kwargs: Dict[str, Any] = {}
    if "scope" in parameters:
        kwargs["scope"] = scope
    if scope is not None:
        scoped_values = {
            "tenant_id": scope.tenant_id,
            "user_id": scope.user_id,
            "owner_user_id": scope.user_id,
        }
        for key, value in scoped_values.items():
            if accepts_kwargs or key in parameters:
                kwargs[key] = value
    return reader(**kwargs)


def _event_payload(record: Any) -> Dict[str, Any]:
    row = _as_mapping(record)
    payload = row.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = None
    if isinstance(payload, Mapping):
        merged = dict(payload)
        for key in ("ingested_seq", "ingested_at"):
            if key in row:
                merged.setdefault(key, row[key])
        return merged
    return row


def _event_timestamp(event: Mapping[str, Any]) -> str:
    return str(
        event.get("created_at")
        or event.get("occurred_at")
        or event.get("timestamp")
        or event.get("ingested_at")
        or ""
    )


def _event_metadata(event: Mapping[str, Any]) -> Dict[str, Any]:
    metadata = _as_mapping(event.get("metadata"))
    metrics = _as_mapping(event.get("metrics"))
    for key, value in metrics.items():
        metadata.setdefault(key, value)
    target = _as_mapping(event.get("target"))
    if target.get("symbol"):
        metadata.setdefault("symbol", target["symbol"])
    for key in (
        "binding_id",
        "runtime_id",
        "source_worker",
        "tenant_id",
        "environment",
        "symbol",
    ):
        if event.get(key) not in (None, ""):
            metadata.setdefault(key, event[key])
    return metadata


def _visible_to_scope(record: Mapping[str, Any], scope: Optional[AgoraCapabilityScope]) -> bool:
    """Preserve user-private filtering without making auth a readiness dependency."""

    tenant_id = _clean_text(record.get("tenant_id"))
    owner_id = _clean_text(record.get("owner_user_id") or record.get("user_id"))
    visibility = str(record.get("visibility") or "").strip().lower()
    if scope is None:
        return not tenant_id and not owner_id or visibility in {"public", "shared"}
    if tenant_id and tenant_id != scope.tenant_id:
        return False
    if owner_id and owner_id != scope.user_id and visibility not in {"public", "shared"}:
        return False
    return True


class ReadStoreAgoraOperationalReadinessProvider:
    """Bind readiness to request-time authoritative, read-only ports.

    Deployments may expose the three explicit ``get_agora_operational_*``
    methods on their read-store facade.  The fallback consumes already-typed
    canonical read methods (telemetry, Source health and Agora list surfaces),
    never a mutation port or process-global readiness fixture.
    """

    _SURFACE_READERS: Dict[str, str] = {
        "signals": "list_agora_signals",
        "journal": "list_decision_journal_entries",
        "inbox": "list_evidence_refs",
    }

    def __init__(
        self,
        get_read_store: Callable[[], Any],
        *,
        telemetry_reader: Optional[Callable[[], List[Dict[str, Any]]]] = None,
        source_reader: Optional[Callable[[Optional[AgoraCapabilityScope]], Any]] = None,
        producer_reader: Optional[Callable[[Optional[AgoraCapabilityScope]], Any]] = None,
        surfaces_reader: Optional[Callable[[Optional[AgoraCapabilityScope]], Any]] = None,
    ) -> None:
        self._get_read_store = get_read_store
        self._telemetry_reader = telemetry_reader
        self._source_reader = source_reader
        self._producer_reader = producer_reader
        self._surfaces_reader = surfaces_reader

    def read(
        self,
        *,
        scope: Optional[AgoraCapabilityScope],
        now_dt: datetime,
    ) -> AgoraOperationalReadinessInputs:
        del now_dt  # freshness is evaluated once by the composition service
        result = AgoraOperationalReadinessInputs()
        try:
            store = self._get_read_store()
            if store is None:
                raise RuntimeError("read store is not configured")
        except Exception:
            result.errors = {
                "source": "read_store_unavailable",
                "producer": "read_store_unavailable",
                "surfaces": "read_store_unavailable",
            }
            return result

        events: List[Dict[str, Any]] = []
        try:
            events = self._read_telemetry(store, scope)
        except Exception:
            result.errors["telemetry"] = "telemetry_provider_unavailable"

        try:
            source = self._read_explicit(
                store,
                "get_agora_operational_readiness_source",
                scope,
                override=self._source_reader,
            )
            if not source:
                source = self._source_from_events(events, scope)
            source_map = _as_mapping(source)
            result.source_snapshot = _as_mapping(source_map.get("snapshot")) or (
                source_map if source_map.get("snapshot_id") else None
            )
            result.source_instance = _as_mapping(source_map.get("instance")) or None
            if result.source_snapshot is None:
                result.errors["source"] = "source_snapshot_unavailable"
        except Exception:
            result.errors["source"] = "source_provider_unavailable"

        try:
            producer = self._read_explicit(
                store,
                "get_agora_operational_readiness_signal_producer",
                scope,
                override=self._producer_reader,
            )
            if not producer:
                producer = self._producer_from_events(events, scope)
            producer_map = _as_mapping(producer)
            if producer_map:
                producer_map["authoritative"] = True
                result.signal_producer = producer_map
            else:
                result.errors["producer"] = "producer_evidence_unavailable"
        except Exception:
            result.errors["producer"] = "producer_provider_unavailable"

        try:
            raw_surfaces = self._read_explicit(
                store,
                "get_agora_operational_readiness_surfaces",
                scope,
                override=self._surfaces_reader,
            )
            result.surfaces = self._normalise_surfaces(raw_surfaces, store, scope)
        except Exception:
            result.errors["surfaces"] = "surfaces_provider_unavailable"
        return result

    def _read_explicit(
        self,
        store: Any,
        method_name: str,
        scope: Optional[AgoraCapabilityScope],
        *,
        override: Optional[Callable[[Optional[AgoraCapabilityScope]], Any]],
    ) -> Any:
        if override is not None:
            return override(scope)
        reader = getattr(store, method_name, None)
        return _call_scoped_reader(reader, scope) if callable(reader) else None

    def _read_telemetry(
        self,
        store: Any,
        scope: Optional[AgoraCapabilityScope],
    ) -> List[Dict[str, Any]]:
        if self._telemetry_reader is not None:
            raw_events = self._telemetry_reader() or []
        else:
            reader = getattr(store, "list_telemetry_events_with_source", None)
            if callable(reader):
                source, raw_events = _call_scoped_reader(reader, scope)
                if str(source) in {"missing", "telemetry_summary_fallback"}:
                    raw_events = []
            else:
                reader = getattr(store, "list_telemetry_events", None)
                raw_events = _call_scoped_reader(reader, scope) if callable(reader) else []
        return [_event_payload(event) for event in (raw_events or []) if isinstance(event, Mapping)]

    @staticmethod
    def _paper_producer_events(
        events: List[Dict[str, Any]],
        scope: Optional[AgoraCapabilityScope],
    ) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
        matched: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
        for event in events:
            event_type = str(event.get("event_type") or event.get("type") or "").strip()
            if event_type != "signal_generation":
                continue
            metadata = _event_metadata(event)
            if metadata.get("source_worker") != "paper-signal-producer":
                continue
            if str(metadata.get("environment") or event.get("environment") or "paper").lower() != "paper":
                continue
            if scope is not None and _clean_text(metadata.get("tenant_id")) != scope.tenant_id:
                continue
            matched.append((event, metadata))
        return sorted(matched, key=lambda item: _event_timestamp(item[0]), reverse=True)

    def _source_from_events(
        self,
        events: List[Dict[str, Any]],
        scope: Optional[AgoraCapabilityScope],
    ) -> Optional[Dict[str, Any]]:
        matched = self._paper_producer_events(events, scope)
        if not matched:
            return None
        _event, metadata = matched[0]
        lineage = _as_mapping(metadata.get("market_input_lineage"))
        source_ids = list(lineage.get("source_ids") or [])
        connector_ids = list(lineage.get("connector_ids") or [])
        source_instance_id = _clean_text(source_ids[0] if source_ids else None) or _clean_text(
            connector_ids[0] if connector_ids else None
        )
        snapshot = {
            "snapshot_id": metadata.get("market_input_snapshot_id"),
            "source_instance_id": source_instance_id,
            "event_time": metadata.get("market_input_event_time"),
            "observed_at": metadata.get("market_input_observed_at"),
            "lineage": lineage,
            "symbol": metadata.get("symbol"),
        }
        instance = {
            "source_instance_id": source_instance_id,
            "desired_state": "enabled",
            "observed_state": "healthy",
        } if source_instance_id else None
        return {"snapshot": snapshot, "instance": instance}

    def _producer_from_events(
        self,
        events: List[Dict[str, Any]],
        scope: Optional[AgoraCapabilityScope],
    ) -> Optional[Dict[str, Any]]:
        matched = self._paper_producer_events(events, scope)
        if not matched:
            return None
        event, metadata = matched[0]
        snapshot_id = _clean_text(metadata.get("market_input_snapshot_id"))
        enqueued = sum(
            1
            for _candidate, candidate_metadata in matched
            if _clean_text(candidate_metadata.get("market_input_snapshot_id")) == snapshot_id
        )
        return {
            "status": "ok",
            "producer_id": "paper-signal-producer",
            "active_binding": metadata.get("binding_id"),
            "consumed_snapshot_id": snapshot_id,
            "last_success_at": _event_timestamp(event),
            "enqueued": enqueued,
            "reason": "canonical_signal_generation_receipt",
        }

    def _normalise_surfaces(
        self,
        raw_surfaces: Any,
        store: Any,
        scope: Optional[AgoraCapabilityScope],
    ) -> Dict[str, Dict[str, Any]]:
        result: Dict[str, Dict[str, Any]] = {}
        if isinstance(raw_surfaces, Mapping):
            for name, value in raw_surfaces.items():
                if isinstance(value, Mapping):
                    result[str(name)] = dict(value)
                elif isinstance(value, list):
                    visible = [item for item in value if isinstance(item, Mapping) and _visible_to_scope(item, scope)]
                    result[str(name)] = {"status": "ok", "count": len(visible)}

        for surface, reader_name in self._SURFACE_READERS.items():
            if surface in result:
                continue
            reader = getattr(store, reader_name, None)
            if not callable(reader):
                result[surface] = {
                    "status": "unavailable",
                    "count": 0,
                    "reason": f"{surface}_reader_unavailable",
                }
                continue
            try:
                rows = _call_scoped_reader(reader, scope) or []
                if isinstance(rows, Mapping):
                    rows = rows.get("items") or rows.get("data") or []
                visible = [
                    item
                    for item in rows
                    if isinstance(item, Mapping) and _visible_to_scope(item, scope)
                ]
                result[surface] = {"status": "ok", "count": len(visible)}
            except Exception:
                result[surface] = {
                    "status": "unavailable",
                    "count": 0,
                    "reason": f"{surface}_provider_unavailable",
                }
        for surface in (
            "signals",
            "decision_events",
            "inbox",
            "journal",
            "candidates",
            "interactions",
            "performance",
        ):
            result.setdefault(
                surface,
                {
                    "status": "unavailable",
                    "count": 0,
                    "reason": f"{surface}_reader_unavailable",
                },
            )
        return result


class EnvironmentAgoraOperationalReadinessProvider:
    """Read the deployed Source/Runtime/telemetry authorities without writes.

    This adapter is enabled only when all three owner locations are configured.
    It performs bounded HTTP GETs and one bounded ``SELECT`` from the canonical
    telemetry table.  The returned producer state therefore proves that the
    active paper binding consumed the exact current Source snapshot; it is not
    inferred from a configured service name or a synthetic default.
    """

    def __init__(
        self,
        get_read_store: Callable[[], Any],
        *,
        runtime_manager_url: Optional[str] = None,
        source_ingest_url: Optional[str] = None,
        telemetry_dsn: Optional[str] = None,
        runtime_manager_token: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        http_get_json: Optional[Callable[[str, Dict[str, str], float], Dict[str, Any]]] = None,
        telemetry_reader: Optional[Callable[[], List[Dict[str, Any]]]] = None,
        surfaces_reader: Optional[Callable[[Optional[AgoraCapabilityScope]], Any]] = None,
    ) -> None:
        self._get_read_store = get_read_store
        self._runtime_manager_url = (
            runtime_manager_url
            if runtime_manager_url is not None
            else os.getenv("PANTHEON_RUNTIME_MANAGER_URL", "")
        ).rstrip("/")
        self._source_ingest_url = (
            source_ingest_url
            if source_ingest_url is not None
            else (
                os.getenv("PANTHEON_SOURCE_INGEST_API_URL")
                or os.getenv("PANTHEON_SOURCE_INGEST_URL")
                or os.getenv("SOURCE_INGEST_URL")
                or ""
            )
        ).rstrip("/")
        self._telemetry_dsn = telemetry_dsn if telemetry_dsn is not None else (
            os.getenv("TELEMETRY_DB_DSN") or os.getenv("DATABASE_URL") or ""
        )
        self._runtime_manager_token = (
            runtime_manager_token
            if runtime_manager_token is not None
            else os.getenv("PANTHEON_RUNTIME_MANAGER_TOKEN", "")
        )
        self._timeout_seconds = timeout_seconds or float(
            os.getenv("PANTHEON_BFF_SERVICE_TIMEOUT_SECONDS", "2.0")
        )
        self._http_get_json = http_get_json or self._default_http_get_json
        self._telemetry_reader_configured = bool(telemetry_reader or self._telemetry_dsn)
        self._telemetry_reader = telemetry_reader or self._read_canonical_telemetry
        self._surface_adapter = ReadStoreAgoraOperationalReadinessProvider(
            get_read_store,
            surfaces_reader=surfaces_reader,
        )

    @property
    def configured(self) -> bool:
        return bool(
            self._runtime_manager_url
            and self._source_ingest_url
            and self._telemetry_reader_configured
        )

    @staticmethod
    def _default_http_get_json(
        url: str,
        headers: Dict[str, str],
        timeout_seconds: float,
    ) -> Dict[str, Any]:
        request = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("authority response must be a JSON object")
        return dict(payload)

    def _read_canonical_telemetry(self) -> List[Dict[str, Any]]:
        if not self._telemetry_dsn:
            raise RuntimeError("canonical telemetry DSN is not configured")
        try:
            import psycopg  # type: ignore[import]
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise RuntimeError("psycopg is required for canonical telemetry reads") from exc
        sql = (
            "SELECT payload, ingested_seq, ingested_at "
            "FROM telemetry_events WHERE event_type=%s "
            "ORDER BY ingested_seq DESC LIMIT 200"
        )
        with psycopg.connect(self._telemetry_dsn, connect_timeout=max(1, int(self._timeout_seconds))) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, ("signal_generation",))
                rows = cursor.fetchall()
        return [
            {
                "payload": row[0],
                "ingested_seq": row[1],
                "ingested_at": row[2],
            }
            for row in rows
        ]

    def _headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._runtime_manager_token:
            headers["Authorization"] = f"Bearer {self._runtime_manager_token}"
        return headers

    @staticmethod
    def _binding_tenant(binding: Mapping[str, Any]) -> Optional[str]:
        metadata = _as_mapping(binding.get("metadata"))
        return _clean_text(binding.get("tenant_id") or metadata.get("tenant_id"))

    def _active_binding(self, scope: Optional[AgoraCapabilityScope]) -> Dict[str, Any]:
        desired_url = f"{self._runtime_manager_url}/api/runtime-fleet/desired-state?stage=paper"
        desired = self._http_get_json(desired_url, self._headers(), self._timeout_seconds)
        candidates = [
            dict(binding)
            for binding in list(desired.get("bindings") or [])
            if isinstance(binding, Mapping) and str(binding.get("status") or "active") == "active"
        ]
        hydrated: List[Dict[str, Any]] = []
        for candidate in candidates:
            binding_id = _clean_text(candidate.get("binding_id"))
            if not binding_id:
                continue
            url = (
                f"{self._runtime_manager_url}/api/runtime-bindings/"
                f"{urllib.parse.quote(binding_id, safe='')}"
            )
            detail = self._http_get_json(url, self._headers(), self._timeout_seconds)
            if detail.get("binding_id") != binding_id or detail.get("status") != "active":
                continue
            tenant_id = self._binding_tenant(detail)
            if scope is not None and tenant_id != scope.tenant_id:
                continue
            hydrated.append(detail)
        if len(hydrated) != 1:
            raise RuntimeError("active_paper_binding_not_unique")
        return hydrated[0]

    @staticmethod
    def _binding_symbol(binding: Mapping[str, Any]) -> str:
        metadata = _as_mapping(binding.get("metadata"))
        market_policy = _as_mapping(binding.get("market_data_policy"))
        symbol = _clean_text(
            binding.get("symbol")
            or metadata.get("symbol")
            or market_policy.get("symbol")
        )
        if symbol:
            return symbol
        symbols = binding.get("symbols") or metadata.get("symbols") or market_policy.get("symbols")
        if isinstance(symbols, list) and len(symbols) == 1:
            symbol = _clean_text(symbols[0])
        if not symbol:
            raise RuntimeError("active_paper_binding_symbol_missing")
        return symbol

    def _source(self, binding: Mapping[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        symbol = self._binding_symbol(binding)
        snapshot_url = (
            f"{self._source_ingest_url}/api/source-ingest/snapshots/latest?"
            f"symbol={urllib.parse.quote(symbol, safe='')}"
        )
        snapshot = self._http_get_json(snapshot_url, {"Accept": "application/json"}, self._timeout_seconds)
        lineage = _as_mapping(snapshot.get("lineage"))
        source_ids = list(lineage.get("source_ids") or [])
        source_instance_id = _clean_text(source_ids[0] if len(source_ids) == 1 else None)
        if not source_instance_id:
            raise RuntimeError("source_instance_identity_not_unique")
        detail_url = (
            f"{self._source_ingest_url}/api/source-ingest/management/sources/"
            f"{urllib.parse.quote(source_instance_id, safe='')}"
        )
        detail = self._http_get_json(detail_url, {"Accept": "application/json"}, self._timeout_seconds)
        source = _as_mapping(detail.get("source"))
        desired = _as_mapping(detail.get("desired"))
        observed = _as_mapping(detail.get("observed"))
        instance = {
            **source,
            "source_instance_id": source_instance_id,
            "desired_state": desired.get("desired_lifecycle") or desired.get("lifecycle_state"),
            "observed_state": (
                observed.get("health_state")
                or observed.get("reconciliation_status")
                or observed.get("effective_lifecycle")
            ),
            "last_failure": observed.get("last_failure") or observed.get("last_error"),
        }
        snapshot["source_instance_id"] = source_instance_id
        return snapshot, instance

    def read(
        self,
        *,
        scope: Optional[AgoraCapabilityScope],
        now_dt: datetime,
    ) -> AgoraOperationalReadinessInputs:
        del now_dt
        result = AgoraOperationalReadinessInputs()
        try:
            store = self._get_read_store()
            if store is None:
                raise RuntimeError("read store is not configured")
        except Exception:
            result.errors["surfaces"] = "read_store_unavailable"
            store = None

        binding: Optional[Dict[str, Any]] = None
        try:
            binding = self._active_binding(scope)
            snapshot, instance = self._source(binding)
            result.source_snapshot = snapshot
            result.source_instance = instance
        except Exception as exc:
            reason = str(exc)
            result.errors["source"] = (
                reason if reason.startswith(("active_", "source_")) else "source_provider_unavailable"
            )

        try:
            events = [
                _event_payload(event)
                for event in (self._telemetry_reader() or [])
                if isinstance(event, Mapping)
            ]
            matched = self._surface_adapter._paper_producer_events(events, scope)
            binding_id = _clean_text(binding.get("binding_id")) if binding else None
            matched = [
                pair
                for pair in matched
                if _clean_text(pair[1].get("binding_id")) == binding_id
            ]
            if not matched:
                raise RuntimeError("producer_consumption_receipt_missing")
            event, metadata = matched[0]
            snapshot_id = _clean_text(metadata.get("market_input_snapshot_id"))
            result.signal_producer = {
                "authoritative": True,
                "status": "ok",
                "producer_id": "paper-signal-producer",
                "active_binding": binding_id,
                "consumed_snapshot_id": snapshot_id,
                "last_success_at": _event_timestamp(event),
                "enqueued": sum(
                    1
                    for _candidate, candidate_metadata in matched
                    if _clean_text(candidate_metadata.get("market_input_snapshot_id")) == snapshot_id
                ),
                "reason": "canonical_signal_generation_receipt",
            }
        except Exception as exc:
            reason = str(exc)
            result.errors["producer"] = (
                reason if reason.startswith("producer_") else "producer_provider_unavailable"
            )

        if store is not None:
            try:
                explicit = self._surface_adapter._read_explicit(
                    store,
                    "get_agora_operational_readiness_surfaces",
                    scope,
                    override=self._surface_adapter._surfaces_reader,
                )
                result.surfaces = self._surface_adapter._normalise_surfaces(explicit, store, scope)
            except Exception:
                result.errors["surfaces"] = "surfaces_provider_unavailable"
        return result


# --------------------------------------------------------------------------- #
# Operational Readiness Service
# --------------------------------------------------------------------------- #

class AgoraOperationalReadinessService:
    """Service to compose Agora operational and data readiness."""

    def __init__(
        self,
        *,
        default_sla_seconds: int = 86400,
        producer_sla_seconds: int = 300,
        clock_skew_seconds: int = 30,
        read_provider: Optional[AgoraOperationalReadinessReadProvider] = None,
        source_provider: Optional[Callable[[], Optional[Dict[str, Any]]]] = None,
        producer_provider: Optional[Callable[[], Optional[Dict[str, Any]]]] = None,
        surfaces_provider: Optional[Callable[[], Optional[Dict[str, Dict[str, Any]]]]] = None,
    ) -> None:
        self.default_sla_seconds = default_sla_seconds
        self.producer_sla_seconds = producer_sla_seconds
        self.clock_skew_seconds = clock_skew_seconds
        self._read_provider = read_provider
        self._source_provider = source_provider
        self._producer_provider = producer_provider
        self._surfaces_provider = surfaces_provider

        # In-memory overrides / state for testing and simulation
        self._custom_source_snapshot: Optional[Dict[str, Any]] = None
        self._custom_source_instance: Optional[Dict[str, Any]] = None
        self._custom_signal_producer: Optional[Dict[str, Any]] = None
        self._custom_surfaces: Dict[str, Dict[str, Any]] = {}

    def set_source_snapshot(self, snapshot: Optional[Dict[str, Any]]) -> None:
        self._custom_source_snapshot = snapshot

    def set_source_instance(self, instance: Optional[Dict[str, Any]]) -> None:
        self._custom_source_instance = instance

    def set_signal_producer(self, producer: Optional[Dict[str, Any]]) -> None:
        self._custom_signal_producer = producer

    def set_surface_data(self, surface_name: str, data: Dict[str, Any]) -> None:
        self._custom_surfaces[surface_name] = data

    def reset_custom_state(self) -> None:
        self._custom_source_snapshot = None
        self._custom_source_instance = None
        self._custom_signal_producer = None
        self._custom_surfaces.clear()

    def _get_source_data(self) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        snapshot = self._custom_source_snapshot
        instance = self._custom_source_instance
        if snapshot is None and self._source_provider is not None:
            prov_data = self._source_provider() or {}
            snapshot = prov_data.get("snapshot") or prov_data
            instance = prov_data.get("instance") or instance
        return snapshot, instance

    def _get_producer_data(self) -> Optional[Dict[str, Any]]:
        if self._custom_signal_producer is not None:
            return self._custom_signal_producer
        if self._producer_provider is not None:
            return self._producer_provider()
        return None

    def _get_surfaces_data(self) -> Dict[str, Dict[str, Any]]:
        surfaces = dict(self._custom_surfaces)
        if self._surfaces_provider is not None:
            prov_surfaces = self._surfaces_provider() or {}
            for k, v in prov_surfaces.items():
                if k not in surfaces:
                    surfaces[k] = v
        return surfaces

    def compose_readiness(
        self,
        *,
        scope: Optional[AgoraCapabilityScope] = None,
        now_dt: Optional[datetime] = None,
        now_iso: Optional[str] = None,
    ) -> AgoraOperationalReadinessEnvelope:
        current_dt = now_dt or (_parse_iso_utc(now_iso) if now_iso else None) or datetime.now(timezone.utc)
        current_iso = now_iso or _format_utc(current_dt)

        provider_errors: Dict[str, str] = {}
        if self._read_provider is not None:
            try:
                inputs = self._read_provider.read(scope=scope, now_dt=current_dt)
            except Exception:
                inputs = AgoraOperationalReadinessInputs(
                    errors={
                        "source": "read_provider_unavailable",
                        "producer": "read_provider_unavailable",
                        "surfaces": "read_provider_unavailable",
                    }
                )
            snapshot = inputs.source_snapshot
            instance = inputs.source_instance
            producer_raw = inputs.signal_producer
            surfaces_raw = inputs.surfaces
            provider_errors = inputs.errors
        else:
            snapshot, instance = self._get_source_data()
            producer_raw = self._get_producer_data()
            surfaces_raw = self._get_surfaces_data()

        # 1. Evaluate Source
        source_readiness = self._evaluate_source(
            snapshot,
            instance,
            current_dt,
            provider_error=provider_errors.get("source"),
        )

        # 2. Evaluate Signal Producer
        producer_readiness = self._evaluate_producer(
            producer_raw,
            source_readiness,
            current_dt,
            provider_error=provider_errors.get("producer") or provider_errors.get("telemetry"),
        )

        # 3. Evaluate Downstream Surfaces
        surfaces_readiness = self._evaluate_surfaces(
            surfaces_raw,
            source_readiness,
            producer_readiness,
            provider_error=provider_errors.get("surfaces"),
        )

        # 4. Compute Overall Status
        overall_status = self._compute_overall_status(
            source_readiness, producer_readiness, surfaces_readiness
        )

        # 5. Deployment Identity
        commit_sha = (
            os.environ.get("PANTHEON_DEPLOYMENT_COMMIT")
            or os.environ.get("GIT_COMMIT_SHA")
            or os.environ.get("SOURCE_COMMIT_SHA")
        )
        deployment = AgoraDeploymentReadiness(
            service="pantheon-bff",
            environment=os.environ.get("PANTHEON_ENV", "dev"),
            source_commit_sha=commit_sha,
            bundle_version=os.environ.get("PANTHEON_BUNDLE_VERSION", "v1.13"),
        )

        data = AgoraOperationalReadinessData(
            status=overall_status,
            source=source_readiness,
            signal_producer=producer_readiness,
            surfaces=surfaces_readiness,
            deployment=deployment,
        )

        audience = None
        if scope:
            audience = f"tenant:{scope.tenant_id}:user:{scope.user_id}"
        else:
            audience = "public:read_only"

        meta = AgoraOperationalReadinessMeta(
            snapshot_at=current_iso,
            capability="agora.operational_readiness.v1",
            requiredForAuthentication=False,
            no_order_route_proof="agora_operational_readiness_read_only",
            audience=audience,
        )

        return AgoraOperationalReadinessEnvelope(data=data, meta=meta)

    def _evaluate_source(
        self,
        snapshot: Optional[Dict[str, Any]],
        instance: Optional[Dict[str, Any]],
        now_dt: datetime,
        *,
        provider_error: Optional[str] = None,
    ) -> AgoraSourceReadiness:
        sla_seconds = self.default_sla_seconds
        if snapshot:
            sla_seconds = int(snapshot.get("sla_seconds") or snapshot.get("max_age_seconds") or sla_seconds)
        elif instance:
            sla_seconds = int(instance.get("sla_seconds") or sla_seconds)

        if not snapshot and not instance:
            return AgoraSourceReadiness(
                snapshot_id=None,
                source_instance_id=None,
                source_timestamp=None,
                age_seconds=None,
                sla_seconds=sla_seconds,
                freshness="unavailable",
                desired_state="not_configured",
                observed_state="unavailable",
                last_failure=provider_error or "no_source_configured",
            )

        snapshot_id = snapshot.get("snapshot_id") if snapshot else None
        source_instance_id = (
            (snapshot.get("source_instance_id") if snapshot else None)
            or (instance.get("source_instance_id") or instance.get("data_source_id") if instance else None)
        )
        source_timestamp = (
            (snapshot.get("source_timestamp") or snapshot.get("event_time") or snapshot.get("timestamp") if snapshot else None)
            or (instance.get("last_successful_run_at") or instance.get("updated_at") if instance else None)
        )
        desired_state = (instance.get("desired_state") or instance.get("lifecycle_state") if instance else "enabled")
        observed_state = (instance.get("observed_state") or instance.get("status") if instance else None)
        last_failure = (instance.get("last_failure") or instance.get("last_error") if instance else None)

        if not source_timestamp:
            return AgoraSourceReadiness(
                snapshot_id=snapshot_id,
                source_instance_id=source_instance_id,
                source_timestamp=None,
                age_seconds=None,
                sla_seconds=sla_seconds,
                freshness="unavailable",
                desired_state=desired_state,
                observed_state=observed_state or "unavailable",
                last_failure=last_failure or "missing_source_timestamp",
            )

        ts_dt = _parse_iso_utc(source_timestamp)
        if not ts_dt:
            return AgoraSourceReadiness(
                snapshot_id=snapshot_id,
                source_instance_id=source_instance_id,
                source_timestamp=source_timestamp,
                age_seconds=None,
                sla_seconds=sla_seconds,
                freshness="unavailable",
                desired_state=desired_state,
                observed_state=observed_state or "invalid_timestamp",
                last_failure=last_failure or "invalid_source_timestamp_format",
            )

        raw_age_seconds = (now_dt - ts_dt).total_seconds()
        age_seconds = max(raw_age_seconds, 0.0)

        # Determine freshness
        freshness: FreshnessState = "fresh"
        symbol = snapshot.get("symbol") if snapshot else None
        if raw_age_seconds < -float(self.clock_skew_seconds):
            freshness = "unavailable"
            observed_state = "invalid_timestamp"
            last_failure = "source_timestamp_in_future"
        elif not snapshot_id:
            freshness = "unavailable"
            observed_state = "unavailable"
            last_failure = last_failure or "missing_snapshot_id"
        elif observed_state in ("degraded", "error"):
            freshness = "degraded"
        elif observed_state in ("unavailable", "unreachable"):
            freshness = "unavailable"
        elif snapshot and is_taiwan_symbol(symbol):
            # Reuse the single governed Taiwan market-session freshness rule
            # (services.execution.market_snapshot_admission) instead of the
            # flat SLA comparison, so a valid Friday official close does not
            # read "stale" here while the execution/reconciler paths admit it.
            refresh_dt = None
            observed_at_raw = snapshot.get("observed_at")
            if observed_at_raw:
                refresh_dt, _err = parse_rfc3339(observed_at_raw, field_name="observed_at")
            ev = snapshot.get("calendar_evidence")
            if ev is None and isinstance(snapshot.get("lineage"), Mapping):
                ev = snapshot["lineage"].get("calendar_evidence")
            tw_ok, _tw_reason, _tw_detail = evaluate_taiwan_market_freshness(
                event_time_dt=ts_dt,
                now_dt=now_dt,
                refresh_receipt_dt=refresh_dt,
                lineage=snapshot.get("lineage"),
                max_refresh_age_seconds=sla_seconds,
                calendar_evidence=ev,
            )
            if not tw_ok:
                freshness = "stale"
            elif snapshot.get("is_empty_fresh") is True:
                freshness = "empty_fresh"
            else:
                freshness = "fresh"
        elif age_seconds > sla_seconds:
            freshness = "stale"
        elif snapshot and snapshot.get("is_empty_fresh") is True:
            freshness = "empty_fresh"
        else:
            freshness = "fresh"

        if not observed_state:
            observed_state = "healthy" if freshness in ("fresh", "empty_fresh") else freshness

        return AgoraSourceReadiness(
            snapshot_id=snapshot_id,
            source_instance_id=source_instance_id,
            source_timestamp=source_timestamp,
            age_seconds=age_seconds,
            sla_seconds=sla_seconds,
            freshness=freshness,
            desired_state=desired_state,
            observed_state=observed_state,
            last_failure=last_failure,
        )

    def _evaluate_producer(
        self,
        raw_producer: Optional[Dict[str, Any]],
        source: AgoraSourceReadiness,
        current_dt: datetime,
        *,
        provider_error: Optional[str] = None,
    ) -> AgoraSignalProducerReadiness:
        producer_id = "paper-signal-producer"
        if not raw_producer:
            if source.freshness == "stale":
                return AgoraSignalProducerReadiness(
                    status="degraded",
                    producer_id=producer_id,
                    last_success_at=None,
                    enqueued=0,
                    reason="source_snapshot_stale",
                )
            if source.freshness == "unavailable":
                return AgoraSignalProducerReadiness(
                    status="unavailable",
                    producer_id=producer_id,
                    last_success_at=None,
                    enqueued=0,
                    reason="source_unavailable",
                )
            return AgoraSignalProducerReadiness(
                status="unavailable",
                producer_id=producer_id,
                last_success_at=None,
                enqueued=0,
                reason=provider_error or "producer_not_configured",
            )

        active_binding = raw_producer.get("active_binding")
        consumed_snapshot = raw_producer.get("consumed_snapshot_id") or raw_producer.get("snapshot_id")
        last_success = raw_producer.get("last_success_at") or raw_producer.get("last_run_at")
        enqueued = int(raw_producer.get("enqueued") or raw_producer.get("queue_count") or 0)
        raw_status = str(raw_producer.get("status") or "ok").lower()
        reason = raw_producer.get("reason")
        authoritative = raw_producer.get("authoritative") is True

        # Upstream source staleness degrades producer
        if source.freshness == "stale":
            return AgoraSignalProducerReadiness(
                status="degraded",
                producer_id=producer_id,
                active_binding=active_binding,
                consumed_snapshot_id=consumed_snapshot,
                last_success_at=last_success,
                enqueued=enqueued,
                reason="source_snapshot_stale",
            )

        if source.freshness == "unavailable":
            return AgoraSignalProducerReadiness(
                status="unavailable",
                producer_id=producer_id,
                active_binding=active_binding,
                consumed_snapshot_id=consumed_snapshot,
                last_success_at=last_success,
                enqueued=enqueued,
                reason="source_unavailable",
            )

        if authoritative:
            if not active_binding:
                return AgoraSignalProducerReadiness(
                    status="unavailable",
                    producer_id=producer_id,
                    consumed_snapshot_id=consumed_snapshot,
                    last_success_at=last_success,
                    enqueued=enqueued,
                    reason="active_binding_missing",
                )
            if not consumed_snapshot:
                return AgoraSignalProducerReadiness(
                    status="unavailable",
                    producer_id=producer_id,
                    active_binding=active_binding,
                    last_success_at=last_success,
                    enqueued=enqueued,
                    reason="consumed_snapshot_missing",
                )
            if consumed_snapshot != source.snapshot_id:
                return AgoraSignalProducerReadiness(
                    status="degraded",
                    producer_id=producer_id,
                    active_binding=active_binding,
                    consumed_snapshot_id=consumed_snapshot,
                    last_success_at=last_success,
                    enqueued=enqueued,
                    reason="consumed_snapshot_mismatch",
                )
            last_success_dt = _parse_iso_utc(last_success)
            if last_success_dt is None:
                return AgoraSignalProducerReadiness(
                    status="unavailable",
                    producer_id=producer_id,
                    active_binding=active_binding,
                    consumed_snapshot_id=consumed_snapshot,
                    enqueued=enqueued,
                    reason="producer_success_timestamp_invalid",
                )
            producer_age = (current_dt - last_success_dt).total_seconds()
            if producer_age < -float(self.clock_skew_seconds):
                return AgoraSignalProducerReadiness(
                    status="unavailable",
                    producer_id=producer_id,
                    active_binding=active_binding,
                    consumed_snapshot_id=consumed_snapshot,
                    last_success_at=last_success,
                    enqueued=enqueued,
                    reason="producer_success_timestamp_in_future",
                )
            if producer_age > float(self.producer_sla_seconds):
                return AgoraSignalProducerReadiness(
                    status="degraded",
                    producer_id=producer_id,
                    active_binding=active_binding,
                    consumed_snapshot_id=consumed_snapshot,
                    last_success_at=last_success,
                    enqueued=enqueued,
                    reason="producer_success_stale",
                )

        if raw_status in ("degraded", "stale", "error"):
            status: ReadinessStatus = "degraded"
            reason = reason or "producer_degraded"
        elif raw_status == "unavailable":
            status = "unavailable"
            reason = reason or "producer_unavailable"
        elif raw_status == "empty_fresh" or (source.freshness == "empty_fresh" and enqueued == 0):
            status = "empty_fresh"
            reason = reason or "rule_evaluation_zero_signals"
        else:
            status = "ok"
            reason = reason or "healthy"

        return AgoraSignalProducerReadiness(
            status=status,
            producer_id=producer_id,
            active_binding=active_binding,
            consumed_snapshot_id=consumed_snapshot if authoritative else (consumed_snapshot or source.snapshot_id),
            last_success_at=last_success or (_format_utc(current_dt) if status == "ok" else None),
            enqueued=enqueued,
            reason=reason,
        )

    def _evaluate_surfaces(
        self,
        surfaces_raw: Dict[str, Dict[str, Any]],
        source: AgoraSourceReadiness,
        producer: AgoraSignalProducerReadiness,
        *,
        provider_error: Optional[str] = None,
    ) -> Dict[str, AgoraSurfaceReadiness]:
        surface_keys = [
            "signals",
            "decision_events",
            "inbox",
            "journal",
            "candidates",
            "interactions",
            "performance",
        ]
        result: Dict[str, AgoraSurfaceReadiness] = {}

        for key in surface_keys:
            raw = surfaces_raw.get(key) or {}
            raw_count = int(raw.get("count") or 0)
            raw_status = raw.get("status")
            raw_reason = raw.get("reason")
            raw_cursor = raw.get("cursor")

            if provider_error and not raw:
                result[key] = AgoraSurfaceReadiness(
                    status="unavailable",
                    count=0,
                    reason=provider_error,
                    freshness="unavailable",
                )
            elif source.freshness == "stale":
                result[key] = AgoraSurfaceReadiness(
                    status="unavailable",
                    count=raw_count,
                    reason="upstream_stale",
                    freshness="stale",
                    cursor=raw_cursor,
                )
            elif source.freshness == "unavailable":
                result[key] = AgoraSurfaceReadiness(
                    status="unavailable",
                    count=raw_count,
                    reason="upstream_unavailable",
                    freshness="unavailable",
                    cursor=raw_cursor,
                )
            elif source.freshness == "degraded" or producer.status == "degraded":
                result[key] = AgoraSurfaceReadiness(
                    status="degraded",
                    count=raw_count,
                    reason=raw_reason or "upstream_degraded",
                    freshness="degraded",
                    cursor=raw_cursor,
                )
            elif producer.status == "empty_fresh" or source.freshness == "empty_fresh" or raw_status == "empty_fresh":
                result[key] = AgoraSurfaceReadiness(
                    status="empty_fresh",
                    count=raw_count,
                    reason=raw_reason or "rule_evaluation_zero_signals",
                    freshness="empty_fresh",
                    cursor=raw_cursor,
                )
            elif raw_status in ("unavailable", "degraded", "ok", "empty_fresh"):
                result[key] = AgoraSurfaceReadiness(
                    status=raw_status,
                    count=raw_count,
                    reason=raw_reason,
                    freshness="fresh" if raw_status == "ok" else raw_status,
                    cursor=raw_cursor,
                )
            else:
                # Default when upstream is healthy
                if raw_count > 0:
                    result[key] = AgoraSurfaceReadiness(
                        status="ok",
                        count=raw_count,
                        reason="healthy",
                        freshness="fresh",
                        cursor=raw_cursor,
                    )
                else:
                    result[key] = AgoraSurfaceReadiness(
                        status="ok",
                        count=0,
                        reason="idle",
                        freshness="fresh",
                        cursor=raw_cursor,
                    )

        return result

    def _compute_overall_status(
        self,
        source: AgoraSourceReadiness,
        producer: AgoraSignalProducerReadiness,
        surfaces: Dict[str, AgoraSurfaceReadiness],
    ) -> ReadinessStatus:
        if source.freshness == "unavailable" or producer.status == "unavailable":
            return "unavailable"
        if source.freshness == "stale" or producer.status == "degraded":
            return "degraded"
        if source.freshness == "degraded":
            return "degraded"
        if any(s.status == "unavailable" for s in surfaces.values()):
            return "degraded"
        if any(s.status == "degraded" for s in surfaces.values()):
            return "degraded"
        if source.freshness == "empty_fresh" or producer.status == "empty_fresh":
            return "empty_fresh"
        if all(s.status == "empty_fresh" for s in surfaces.values()):
            return "empty_fresh"
        return "ok"


# --------------------------------------------------------------------------- #
# Global default service instance
# --------------------------------------------------------------------------- #

_DEFAULT_SERVICE = AgoraOperationalReadinessService()


def get_default_operational_readiness_service() -> AgoraOperationalReadinessService:
    return _DEFAULT_SERVICE


# --------------------------------------------------------------------------- #
# Router Factory
# --------------------------------------------------------------------------- #

def create_operational_readiness_router(
    *,
    extract_identity: Optional[Callable[..., Any]] = None,
    require_read_role: Optional[Callable[..., None]] = None,
    bff_error: Optional[Callable[..., HTTPException]] = None,
    utc_now: Callable[[], str],
    service: Optional[AgoraOperationalReadinessService] = None,
    get_read_store: Optional[Callable[[], Any]] = None,
    read_provider: Optional[AgoraOperationalReadinessReadProvider] = None,
    surfaces_reader: Optional[Callable[[Optional[AgoraCapabilityScope]], Any]] = None,
) -> APIRouter:
    """Create the Agora Operational Readiness router."""
    router = APIRouter(tags=["agora-operational-readiness"])
    if service is not None:
        readiness_svc = service
    elif read_provider is not None:
        readiness_svc = AgoraOperationalReadinessService(read_provider=read_provider)
    elif get_read_store is not None:
        environment_provider = EnvironmentAgoraOperationalReadinessProvider(
            get_read_store,
            surfaces_reader=surfaces_reader,
        )
        provider: AgoraOperationalReadinessReadProvider = (
            environment_provider
            if environment_provider.configured
            else ReadStoreAgoraOperationalReadinessProvider(
                get_read_store,
                surfaces_reader=surfaces_reader,
            )
        )
        readiness_svc = AgoraOperationalReadinessService(read_provider=provider)
    else:
        readiness_svc = _DEFAULT_SERVICE

    @router.get("/bff/agora/operational-readiness")
    def agora_operational_readiness(
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    ) -> Dict[str, Any]:
        """Return composed Agora operational and data readiness readback.

        This endpoint is strictly read-only and never auth-critical
        (requiredForAuthentication=False). It is never called by /bff/auth/readiness.
        """
        scope: Optional[AgoraCapabilityScope] = None
        if authorization and extract_identity:
            try:
                identity = extract_identity(authorization)
                if require_read_role:
                    require_read_role(identity)
                scope = resolve_agora_user_scope(
                    identity,
                    utc_now=utc_now,
                    requested_tenant_id=x_tenant_id or x_pantheon_tenant,
                )
            except Exception:
                # Operational readiness is non-auth-critical; ignore identity errors
                scope = None

        envelope = readiness_svc.compose_readiness(
            scope=scope,
            now_iso=utc_now(),
        )
        return envelope.model_dump()

    return router
