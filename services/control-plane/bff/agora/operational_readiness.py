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
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Literal, Mapping, Optional, Tuple

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


# --------------------------------------------------------------------------- #
# Operational Readiness Service
# --------------------------------------------------------------------------- #

class AgoraOperationalReadinessService:
    """Service to compose Agora operational and data readiness."""

    def __init__(
        self,
        *,
        default_sla_seconds: int = 86400,
        source_provider: Optional[Callable[[], Optional[Dict[str, Any]]]] = None,
        producer_provider: Optional[Callable[[], Optional[Dict[str, Any]]]] = None,
        surfaces_provider: Optional[Callable[[], Optional[Dict[str, Dict[str, Any]]]]] = None,
    ) -> None:
        self.default_sla_seconds = default_sla_seconds
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

        snapshot, instance = self._get_source_data()
        producer_raw = self._get_producer_data()
        surfaces_raw = self._get_surfaces_data()

        # 1. Evaluate Source
        source_readiness = self._evaluate_source(snapshot, instance, current_dt)

        # 2. Evaluate Signal Producer
        producer_readiness = self._evaluate_producer(producer_raw, source_readiness, current_iso)

        # 3. Evaluate Downstream Surfaces
        surfaces_readiness = self._evaluate_surfaces(surfaces_raw, source_readiness, producer_readiness)

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
                last_failure="no_source_configured",
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

        age_seconds = max((now_dt - ts_dt).total_seconds(), 0.0)

        # Determine freshness
        freshness: FreshnessState = "fresh"
        symbol = snapshot.get("symbol") if snapshot else None
        if observed_state in ("degraded", "error"):
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
        current_iso: str,
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
                reason="producer_not_configured",
            )

        active_binding = raw_producer.get("active_binding")
        consumed_snapshot = raw_producer.get("consumed_snapshot_id") or raw_producer.get("snapshot_id")
        last_success = raw_producer.get("last_success_at") or raw_producer.get("last_run_at")
        enqueued = int(raw_producer.get("enqueued") or raw_producer.get("queue_count") or 0)
        raw_status = str(raw_producer.get("status") or "ok").lower()
        reason = raw_producer.get("reason")

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
            consumed_snapshot_id=consumed_snapshot or source.snapshot_id,
            last_success_at=last_success or (current_iso if status == "ok" else None),
            enqueued=enqueued,
            reason=reason,
        )

    def _evaluate_surfaces(
        self,
        surfaces_raw: Dict[str, Dict[str, Any]],
        source: AgoraSourceReadiness,
        producer: AgoraSignalProducerReadiness,
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

            if source.freshness == "stale":
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
) -> APIRouter:
    """Create the Agora Operational Readiness router."""
    router = APIRouter(tags=["agora-operational-readiness"])
    readiness_svc = service or _DEFAULT_SERVICE

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
