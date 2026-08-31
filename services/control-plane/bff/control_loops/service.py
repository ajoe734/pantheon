"""Thin read adapter for the Control Loops router.

The adapter deliberately owns no command ledger, command validation, local
loop registry, or controller state.  Reads are composed from the existing BFF
domain ports, ``loop_inventory`` catalog, ``loop_truth`` projection, and trade
journey lifecycle projection.  Write routes are wired directly to the
canonical command admission callables by :mod:`control_loops.router`.
"""
from __future__ import annotations

import inspect
import os
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from fastapi import HTTPException

try:
    from management_read_models import loop_truth as default_loop_truth
except ImportError:
    from ..management_read_models import loop_truth as default_loop_truth  # type: ignore[no-redef]

try:
    from loop_inventory import (
        get_loop_inventory_entry,
        list_loop_inventory_entries,
        loop_inventory_meta,
        truth_label_payload,
    )
    from trade_journey_projection_store import InvalidPageToken, ProjectionReadUnavailable
except ImportError:
    from ..loop_inventory import (  # type: ignore[no-redef]
        get_loop_inventory_entry,
        list_loop_inventory_entries,
        loop_inventory_meta,
        truth_label_payload,
    )
    from ..trade_journey_projection_store import (  # type: ignore[no-redef]
        InvalidPageToken,
        ProjectionReadUnavailable,
    )

try:
    from models import ErrorCode
except ImportError:
    from ..models import ErrorCode  # type: ignore[no-redef]


HealthFindingsProvider = Callable[..., List[Dict[str, Any]]]
InterventionRecordsProvider = Callable[..., List[Dict[str, Any]]]

_LOOP_RUN_PROJECTION_SCHEMA = "pantheon.loop-run-projection.v1"
_VALID_SENTINEL_FILTERS = {
    "kind": {"hiq_sentinel", "risk_breach", "strategy_drift", "loop_anomaly", "persona_health"},
    "status": {"open", "resolved", "dismissed", "escalated"},
    "severity": {"critical", "high", "medium", "low"},
}
_OODA_STAGE_STATUSES = {
    "observe": {"open", "observing"},
    "orient": {"oriented"},
    "decide": {"decided"},
    "act": {"acted"},
    "learn": {"evolving"},
}


def utc_now_rfc3339() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_bff_error(
    status_code: int,
    code: Any,
    message: str,
    reason: Optional[str] = None,
    *,
    precondition_failed: Optional[str] = None,
    suggestion: Optional[str] = None,
    details_extra: Optional[Dict[str, Any]] = None,
) -> HTTPException:
    """Build the canonical error shape without importing ``main.py``."""

    code_value = getattr(code, "value", str(code))
    error: Dict[str, Any] = {
        "code": code_value,
        "message": message,
        "reason": reason or message,
        "status_code": status_code,
    }
    details: Dict[str, Any] = {}
    if precondition_failed:
        details["precondition_failed"] = precondition_failed
    if details_extra:
        details.update(details_extra)
    if details:
        error["details"] = details
    if suggestion:
        error["suggestion"] = suggestion
    return HTTPException(status_code=status_code, detail={"error": error})


async def _resolve(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _dedupe_strings(values: Iterable[Any]) -> List[str]:
    result: List[str] = []
    for value in values:
        if isinstance(value, Mapping):
            value = value.get("id") or value.get("value") or value.get("name")
        if isinstance(value, (list, tuple, set)):
            for nested in _dedupe_strings(value):
                if nested not in result:
                    result.append(nested)
            continue
        clean = str(value or "").strip()
        if clean and clean not in result:
            result.append(clean)
    return result


def _claim_strings(identity: Any, *names: str) -> List[str]:
    claims = getattr(identity, "claims", {})
    if not isinstance(claims, Mapping):
        return []
    values: List[Any] = []
    for name in names:
        current: Any = claims
        for part in name.split("."):
            if not isinstance(current, Mapping):
                current = None
                break
            current = current.get(part)
        if isinstance(current, str):
            values.extend(part for part in current.replace(",", " ").split() if part)
        elif isinstance(current, (list, tuple, set)):
            values.extend(current)
        elif current is not None:
            values.append(current)
    return _dedupe_strings(values)


class _MissingReadPort:
    def dataset_source(self, _dataset: str) -> str:
        return "missing"


class ControlLoopsService:
    """Compose route-facing reads from existing domain owners only."""

    def __init__(
        self,
        *,
        read_store: Optional[Any] = None,
        loop_truth_adapter: Optional[Any] = None,
        downstream_health_monitor: Optional[Any] = None,
        health_findings_provider: Optional[HealthFindingsProvider] = None,
        intervention_records_provider: Optional[InterventionRecordsProvider] = None,
        utc_now_fn: Optional[Callable[[], str]] = None,
        bff_error_fn: Optional[Callable[..., Exception]] = None,
        deployed_environment: Optional[str] = None,
    ) -> None:
        self.read_store = read_store or _MissingReadPort()
        self.loop_truth = loop_truth_adapter or default_loop_truth
        self.downstream_health_monitor = downstream_health_monitor
        self.health_findings_provider = health_findings_provider
        self.intervention_records_provider = intervention_records_provider
        self.utc_now = utc_now_fn or utc_now_rfc3339
        self.bff_error = bff_error_fn or default_bff_error
        self.deployed_environment = str(
            deployed_environment
            if deployed_environment is not None
            else os.environ.get("PANTHEON_ENV", "dev")
        ).strip()

    def _error(self, *args: Any, **kwargs: Any) -> Exception:
        return self.bff_error(*args, **kwargs)

    def dataset_source(self, dataset: str) -> str:
        source = getattr(self.read_store, "dataset_source", None)
        if not callable(source):
            return "missing"
        try:
            return str(source(dataset) or "missing")
        except (OSError, TypeError, ValueError):
            return "missing"

    def _surface(
        self,
        dataset: str,
        *,
        source: Optional[str] = None,
        available: Optional[bool] = None,
        snapshot_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        resolved_source = source or self.dataset_source(dataset)
        status = "ok"
        if available is False or resolved_source in {"missing", "unavailable"}:
            status = "unavailable"
        elif resolved_source in {"local_snapshot", "incident_reconstruction"}:
            status = "degraded"
        surface: Dict[str, Any] = {"status": status, "source": resolved_source}
        if status != "ok":
            surface["staleness"] = {
                "served_from": resolved_source if resolved_source != "missing" else "unverifiable",
                "last_known_at": snapshot_at or self.utc_now(),
            }
        return surface

    def _list_envelope(
        self,
        records: Sequence[Mapping[str, Any]],
        *,
        dataset: str,
        surface_key: str,
        source: Optional[str] = None,
        surface: Optional[Dict[str, Any]] = None,
        next_page_token: Optional[str] = None,
        total: Optional[int] = None,
    ) -> Dict[str, Any]:
        items = [deepcopy(dict(record)) for record in records]
        return {
            "data": items,
            "items": items,
            "page_info": {
                "next_page_token": next_page_token,
                "total": len(items) if total is None else total,
            },
            "meta": {
                "snapshot_at": self.utc_now(),
                "total": len(items) if total is None else total,
                "surfaces": {
                    surface_key: surface or self._surface(dataset, source=source)
                },
            },
        }

    def _detail(
        self,
        record: Optional[Mapping[str, Any]],
        *,
        entity_id: str,
        label: str,
        dataset: str,
        surface_key: str,
        source: Optional[str] = None,
        available: Optional[bool] = None,
        surface: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        resolved_surface = surface or self._surface(
            dataset, source=source, available=available
        )
        if record is not None:
            return {
                "data": deepcopy(dict(record)),
                "meta": {
                    "snapshot_at": self.utc_now(),
                    "surfaces": {surface_key: resolved_surface},
                },
            }
        if available is False or resolved_surface.get("status") == "unavailable":
            return {
                "data": {
                    "id": entity_id,
                    "status": "degraded",
                    "readSurface": resolved_surface,
                    "message": f"{label} read model source is unavailable.",
                },
                "meta": {
                    "snapshot_at": self.utc_now(),
                    "surfaces": {surface_key: resolved_surface},
                },
            }
        raise self._error(
            404,
            ErrorCode.RESOURCE_NOT_FOUND,
            f"{label} not found",
            f"{label} {entity_id} does not exist",
        )

    def _call_records(self, names: Sequence[str], **filters: Any) -> List[Dict[str, Any]]:
        for name in names:
            method = getattr(self.read_store, name, None)
            if not callable(method):
                continue
            try:
                result = method(**filters)
            except TypeError:
                result = method()
                if isinstance(result, tuple) and len(result) == 2:
                    result = result[1]
                for key, value in filters.items():
                    if value is None:
                        continue
                    result = [
                        item
                        for item in result or []
                        if str((item or {}).get(key) or "").lower() == str(value).lower()
                    ]
            if isinstance(result, tuple) and len(result) == 2:
                result = result[1]
            return [dict(item) for item in result or [] if isinstance(item, Mapping)]
        return []

    @staticmethod
    def _page(
        records: Sequence[Dict[str, Any]],
        page_token: Optional[str],
        page_size: int,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        try:
            offset = int(page_token) if page_token else 0
        except (TypeError, ValueError) as exc:
            raise default_bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Invalid page_token",
                "page_token must be a non-negative integer offset",
            ) from exc
        if offset < 0:
            raise default_bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Invalid page_token",
                "page_token must be a non-negative integer offset",
            )
        end = offset + page_size
        return list(records[offset:end]), str(end) if end < len(records) else None

    @staticmethod
    def ooda_routes_enabled() -> bool:
        raw = os.environ.get("PANTHEON_OODA_PACKET_ENABLED")
        return raw is None or raw.strip().lower() not in {"0", "false", "no", "off", "disabled"}

    def _require_ooda_routes_enabled(self) -> None:
        if not self.ooda_routes_enabled():
            raise self._error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "OODA packet read routes disabled",
                "PANTHEON_OODA_PACKET_ENABLED is disabled for this BFF instance.",
                precondition_failed="ooda_packet_feature_flag",
            )

    def list_ooda_packets(
        self,
        *,
        status: Optional[str],
        stage: Optional[str],
        strategy_id: Optional[str],
        runtime_id: Optional[str],
        evolution_program_id: Optional[str],
        page_token: Optional[str],
        page_size: int,
    ) -> Dict[str, Any]:
        self._require_ooda_routes_enabled()
        records = self._call_records(
            ("list_ooda_packets",),
            status=status,
            stage=stage,
            strategy_id=strategy_id,
            runtime_id=runtime_id,
            evolution_program_id=evolution_program_id,
        )
        page, next_token = self._page(records, page_token, page_size)
        return self._list_envelope(
            page,
            dataset="ooda_packets",
            surface_key="ooda_packets",
            next_page_token=next_token,
            total=len(records),
        )

    def get_ooda_packet(self, packet_id: str) -> Dict[str, Any]:
        self._require_ooda_routes_enabled()
        getter = getattr(self.read_store, "get_ooda_packet", None)
        record = getter(packet_id) if callable(getter) else None
        source = self.dataset_source("ooda_packets")
        return self._detail(
            record if isinstance(record, Mapping) else None,
            entity_id=packet_id,
            label="OODA packet",
            dataset="ooda_packets",
            surface_key="ooda_packet_detail",
            source=source,
            available=False if source == "missing" else None,
        )

    def intervention_records(
        self,
        *,
        status: Optional[str] = None,
        kind: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if self.intervention_records_provider is not None:
            records = self.intervention_records_provider(status=status, kind=kind)
            return [dict(item) for item in records or [] if isinstance(item, Mapping)]
        return self._call_records(
            ("list_interventions", "list_v5_interventions"),
            status=status,
            kind=kind,
        )

    def list_interventions(
        self,
        *,
        status: Optional[str],
        kind: Optional[str],
    ) -> Dict[str, Any]:
        records = self.intervention_records(status=status, kind=kind)
        return {"items": records, "count": len(records), "generated_at": self.utc_now()}

    def get_intervention(self, intervention_id: str) -> Dict[str, Any]:
        clean_id = str(intervention_id or "").strip()
        getter = getattr(self.read_store, "get_intervention", None)
        record = getter(clean_id) if callable(getter) else None
        if record is None:
            record = next(
                (
                    item
                    for item in self.intervention_records()
                    if str(item.get("intervention_id") or item.get("id") or "").strip()
                    == clean_id
                ),
                None,
            )
        return self._detail(
            record if isinstance(record, Mapping) else None,
            entity_id=clean_id,
            label="Intervention",
            dataset="interventions",
            surface_key="intervention_detail",
            source="domain_port",
            available=True,
        )

    def _available_records(self, method_name: str, **filters: Any) -> Tuple[bool, List[Dict[str, Any]]]:
        method = getattr(self.read_store, method_name, None)
        if not callable(method):
            return False, []
        result = method(**filters)
        if isinstance(result, tuple) and len(result) == 2:
            return bool(result[0]), [
                dict(item) for item in result[1] or [] if isinstance(item, Mapping)
            ]
        return True, [dict(item) for item in result or [] if isinstance(item, Mapping)]

    def list_sentinel_findings(
        self,
        *,
        kind: Optional[str],
        status: Optional[str],
        severity: Optional[str],
        tenant_id: Optional[str],
    ) -> Dict[str, Any]:
        for field, value in (("kind", kind), ("status", status), ("severity", severity)):
            if value is not None and value.lower() not in _VALID_SENTINEL_FILTERS[field]:
                raise self._error(
                    400,
                    ErrorCode.VALIDATION_FAILED,
                    f"Invalid {field} '{value}'",
                    f"Unknown sentinel finding {field} filter value",
                    precondition_failed=field,
                )
        available, records = self._available_records(
            "list_sentinel_findings", kind=kind, status=status, severity=severity
        )
        if self.health_findings_provider is not None:
            derived = self.health_findings_provider(
                kind=kind, status=status, severity=severity, tenant_id=tenant_id
            )
            existing = {str(item.get("id") or item.get("finding_id") or "") for item in records}
            records.extend(
                dict(item)
                for item in derived or []
                if isinstance(item, Mapping)
                and str(item.get("id") or item.get("finding_id") or "") not in existing
            )
            available = available or bool(derived)
        source = self.dataset_source("sentinel_findings")
        if source == "missing" and available:
            source = self.dataset_source("incidents")
        return self._list_envelope(
            records,
            dataset="sentinel_findings",
            surface_key="sentinel_findings",
            source=source,
            surface=self._surface("sentinel_findings", source=source, available=available),
        )

    def get_sentinel_finding(self, finding_id: str) -> Dict[str, Any]:
        getter = getattr(self.read_store, "get_sentinel_finding", None)
        result = getter(finding_id) if callable(getter) else (False, None)
        if isinstance(result, tuple) and len(result) == 2:
            available, record = bool(result[0]), result[1]
        else:
            record, available = result, result is not None
        if record is None and self.health_findings_provider is not None:
            derived = self.health_findings_provider(
                kind=None, status=None, severity=None, tenant_id=None
            )
            record = next(
                (
                    item
                    for item in derived or []
                    if str((item or {}).get("id") or (item or {}).get("finding_id") or "")
                    == finding_id
                ),
                None,
            )
            available = available or bool(derived)
        source = self.dataset_source("sentinel_findings")
        return self._detail(
            record if isinstance(record, Mapping) else None,
            entity_id=finding_id,
            label="Sentinel finding",
            dataset="sentinel_findings",
            surface_key="sentinel_finding_detail",
            source=source,
            available=available,
        )

    @staticmethod
    def _inventory_meta(payload: Dict[str, Any]) -> Dict[str, Any]:
        meta = payload.setdefault("meta", {})
        meta.setdefault("surfaces", {}).setdefault("loop_inventory", {}).update(
            {
                "status": "ok",
                "source": "bff_local_registry",
                "truth_level": "registry_metadata",
                "registry_ref": "docs/deployment/loop-catalog.registry.json",
            }
        )
        meta["catalog"] = loop_inventory_meta()
        return payload

    def loop_inventory(self) -> Dict[str, Any]:
        return self._inventory_meta(
            self._list_envelope(
                list_loop_inventory_entries(),
                dataset="loop_inventory",
                surface_key="loop_inventory",
                source="bff_local_registry",
            )
        )

    def loop_inventory_detail(self, loop_id: str) -> Dict[str, Any]:
        record = get_loop_inventory_entry(loop_id)
        if record is None:
            raise self._error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Loop inventory entry not found",
                f"Loop inventory entry {loop_id} does not exist",
            )
        return self._inventory_meta(
            self._detail(
                record,
                entity_id=loop_id,
                label="Loop inventory entry",
                dataset="loop_inventory",
                surface_key="loop_inventory",
                source="bff_local_registry",
                available=True,
            )
        )

    def authenticated_loop_truth_scope(
        self,
        identity: Any,
        *,
        requested_tenant: Optional[str],
        requested_environment: Optional[str],
    ) -> Tuple[str, str]:
        defaults = _claim_strings(identity, "tenant_id", "tenantId", "tenant.id", "tid", "org_id")
        allowed_tenants = _dedupe_strings(
            [
                *_claim_strings(
                    identity,
                    "allowed_tenants",
                    "allowedTenants",
                    "tenant_ids",
                    "tenantIds",
                    "tenants",
                ),
                *defaults,
            ]
        )
        tenant_id = str(requested_tenant or "").strip() or next(
            (value for value in defaults + allowed_tenants if value != "*"), ""
        )
        if not tenant_id:
            raise self._error(
                403,
                ErrorCode.FORBIDDEN,
                "Controller truth requires an authenticated tenant scope",
                "Authenticated identity does not declare a concrete tenant",
                precondition_failed="tenant_scope",
            )
        if "*" not in allowed_tenants and tenant_id not in allowed_tenants:
            raise self._error(
                403,
                ErrorCode.FORBIDDEN,
                "Tenant access denied",
                "Requested controller truth tenant is outside the authenticated scope",
                precondition_failed="tenant_scope",
            )
        environment = str(requested_environment or "").strip() or self.deployed_environment
        allowed_environments = _claim_strings(
            identity, "environment", "environments", "allowed_environments", "allowedEnvironments"
        )
        if allowed_environments:
            environment_allowed = "*" in allowed_environments or environment in allowed_environments
        else:
            environment_allowed = environment == self.deployed_environment
        if not environment or not environment_allowed:
            raise self._error(
                403,
                ErrorCode.FORBIDDEN,
                "Environment access denied",
                "Requested controller truth environment is outside the authenticated deployment scope",
                precondition_failed="environment_scope",
            )
        return tenant_id, environment

    def _loop_health_meta(
        self,
        payload: Dict[str, Any],
        *,
        available: bool,
        raw_count: int,
        source: str,
        tenant_id: str,
        environment: str,
    ) -> Dict[str, Any]:
        items = payload.get("items") or [payload.get("data")]
        accepted = sum(
            1
            for item in items
            if isinstance(item, Mapping)
            and (item.get("controller_health") or {}).get("current_record_accepted") is True
        )
        catalog = loop_inventory_meta()
        meta = payload.setdefault("meta", {})
        surfaces = meta.setdefault("surfaces", {})
        surfaces["loop_health"] = self._surface(
            "loop_health", source=source, available=available
        )
        if not available:
            # The twelve catalog rows remain useful as registry metadata, but
            # must never be promoted to current controller truth.
            surfaces["loop_health"]["status"] = "degraded"
        surfaces["loop_health"].update(
            {
                "truth_level": "controller_store" if accepted else "registry_metadata",
                "accepted_live": bool(accepted),
            }
        )
        surfaces["loop_inventory"] = {
            "status": "ok",
            "source": "bff_local_registry",
            "truth_level": "registry_metadata",
            "registry_ref": "docs/deployment/loop-catalog.registry.json",
        }
        meta.update(
            {
                "catalog": catalog,
                "truth_labels": truth_label_payload(),
                "coverage": {
                    "loop_count": len(items),
                    "canonical_loop_count": catalog["inventory_counts"]["canonical_loop_count"],
                    "controller_health_record_count": accepted,
                    "raw_health_record_count": raw_count,
                    "controller_health_records_available": available,
                },
                "scope": {
                    "tenant_id": tenant_id,
                    "environment": environment,
                    "source": "authenticated_identity_and_deployment_scope",
                },
            }
        )
        return payload

    async def loop_health(
        self,
        identity: Any,
        *,
        requested_tenant: Optional[str],
        requested_environment: Optional[str],
    ) -> Dict[str, Any]:
        tenant_id, environment = self.authenticated_loop_truth_scope(
            identity,
            requested_tenant=requested_tenant,
            requested_environment=requested_environment,
        )
        available, raw_records = await self.loop_truth.fetch_controller_store_health_records(
            tenant_id, environment
        )
        source = "controller_store" if available else "missing"
        records = self.loop_truth.project_canonical_loop_health(
            raw_records, health_source=source
        )
        return self._loop_health_meta(
            self._list_envelope(
                records,
                dataset="loop_health",
                surface_key="loop_health",
                source=source,
            ),
            available=available,
            raw_count=len(raw_records),
            source=source,
            tenant_id=tenant_id,
            environment=environment,
        )

    async def loop_health_detail(
        self,
        loop_id: str,
        identity: Any,
        *,
        requested_tenant: Optional[str],
        requested_environment: Optional[str],
    ) -> Dict[str, Any]:
        tenant_id, environment = self.authenticated_loop_truth_scope(
            identity,
            requested_tenant=requested_tenant,
            requested_environment=requested_environment,
        )
        available, raw_records = await self.loop_truth.fetch_controller_store_health_records(
            tenant_id, environment
        )
        source = "controller_store" if available else "missing"
        record = self.loop_truth.project_canonical_loop_health_entry(
            loop_id, raw_records, health_source=source
        )
        if record is None:
            raise self._error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Loop health entry not found",
                f"Loop health entry {loop_id} does not exist",
            )
        return self._loop_health_meta(
            self._detail(
                record,
                entity_id=loop_id,
                label="Loop health entry",
                dataset="loop_health",
                surface_key="loop_health",
                source=source,
                available=available,
            ),
            available=available,
            raw_count=len(raw_records),
            source=source,
            tenant_id=tenant_id,
            environment=environment,
        )

    def _loop_run_surface(self, available: bool) -> Dict[str, Any]:
        source = self.dataset_source("loop_runs")
        surface = self._surface("loop_runs", source=source, available=available)
        metadata_fn = getattr(self.read_store, "loop_run_projection_metadata", None)
        metadata = metadata_fn() if callable(metadata_fn) else {}
        metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
        controller = metadata.get("controller")
        controller = dict(controller) if isinstance(controller, Mapping) else {}
        formal = (
            metadata.get("schema_version") == _LOOP_RUN_PROJECTION_SCHEMA
            and controller.get("accepted_live") is True
            and str(controller.get("status") or "").lower() == "ready"
            and str(controller.get("mode") or "").lower() == "live"
            and str(controller.get("truth_level") or "").lower() == "canonical_live"
        )
        surface.update(
            {
                "projection_schema_version": metadata.get("schema_version"),
                "projection_generation": metadata.get("generation"),
                "controller": controller,
                "accepted_live": controller.get("accepted_live"),
                "projection_mode": controller.get("mode"),
                "truth_level": controller.get("truth_level"),
                "truth_status": "formal" if formal and surface["status"] == "ok" else "degraded",
            }
        )
        if not formal:
            surface["status"] = "degraded" if available else "unavailable"
        return surface

    def _projection_reader(self) -> Any:
        provider = getattr(self.read_store, "trade_journey_projection_reader", None)
        return provider() if callable(provider) else None

    async def list_loop_runs(
        self,
        identity: Any,
        *,
        status: Optional[str],
        tenant_id: Optional[str],
        environment: Optional[str],
        page_token: Optional[str],
        page_size: int,
    ) -> Dict[str, Any]:
        projection = self._projection_reader()
        if projection is not None:
            scoped_tenant, scoped_environment = self.authenticated_loop_truth_scope(
                identity,
                requested_tenant=tenant_id,
                requested_environment=environment,
            )
            statuses = sorted(
                {part.strip().lower() for part in (status or "").split(",") if part.strip()}
            )
            try:
                records, next_token = projection.page_loop_runs(
                    tenant_id=scoped_tenant,
                    environment=scoped_environment,
                    statuses=statuses,
                    page_size=page_size,
                    page_token=page_token,
                )
                controller = projection.controller_freshness(
                    tenant_id=scoped_tenant, environment=scoped_environment
                ) or {}
            except InvalidPageToken as exc:
                raise self._error(
                    422,
                    ErrorCode.VALIDATION_FAILED,
                    "Invalid page_token",
                    "page_token does not match this tenant/environment scope",
                ) from exc
            except ProjectionReadUnavailable:
                return self._list_envelope(
                    [], dataset="loop_runs", surface_key="loop_runs", source="missing"
                )
            formal = (
                controller.get("accepted_live") is True
                and controller.get("status") == "ready"
                and controller.get("mode") == "live"
            )
            surface = {
                "status": "ok" if formal else "degraded",
                "source": "postgres_lifecycle_projection",
                "projection_schema_version": "pantheon.trade-journey-projection.v1",
                "controller": controller,
                "accepted_live": controller.get("accepted_live"),
                "projection_mode": controller.get("mode"),
                "truth_status": "formal" if formal else "degraded",
            }
            response = self._list_envelope(
                records,
                dataset="loop_runs",
                surface_key="loop_runs",
                source="postgres_lifecycle_projection",
                surface=surface,
                next_page_token=next_token,
            )
            response["page_info"].update(
                {"page_size": page_size, "returned": len(records), "has_more": next_token is not None}
            )
            return response

        available, records = self._available_records("list_loop_runs")
        if status:
            requested = {part.strip().lower() for part in status.split(",") if part.strip()}
            records = [
                record
                for record in records
                if str(record.get("status") or "").lower() in requested
            ]
        return self._list_envelope(
            records,
            dataset="loop_runs",
            surface_key="loop_runs",
            surface=self._loop_run_surface(available),
        )

    async def get_loop_run(
        self,
        loop_run_id: str,
        identity: Any,
        *,
        tenant_id: Optional[str],
        environment: Optional[str],
    ) -> Dict[str, Any]:
        projection = self._projection_reader()
        if projection is not None:
            scoped_tenant, scoped_environment = self.authenticated_loop_truth_scope(
                identity,
                requested_tenant=tenant_id,
                requested_environment=environment,
            )
            try:
                record = projection.get_loop_run(
                    tenant_id=scoped_tenant,
                    environment=scoped_environment,
                    loop_run_id=loop_run_id,
                )
                controller = projection.controller_freshness(
                    tenant_id=scoped_tenant, environment=scoped_environment
                ) or {}
            except ProjectionReadUnavailable:
                return self._detail(
                    None,
                    entity_id=loop_run_id,
                    label="Loop run",
                    dataset="loop_runs",
                    surface_key="loop_run_detail",
                    source="missing",
                    available=False,
                )
            formal = (
                controller.get("accepted_live") is True
                and controller.get("status") == "ready"
                and controller.get("mode") == "live"
            )
            surface = {
                "status": "ok" if formal else "degraded",
                "source": "postgres_lifecycle_projection",
                "controller": controller,
                "accepted_live": controller.get("accepted_live"),
                "truth_status": "formal" if formal else "degraded",
            }
            return self._detail(
                record if isinstance(record, Mapping) else None,
                entity_id=loop_run_id,
                label="Loop run",
                dataset="loop_runs",
                surface_key="loop_run_detail",
                source="postgres_lifecycle_projection",
                available=True,
                surface=surface,
            )

        getter = getattr(self.read_store, "get_loop_run", None)
        result = getter(loop_run_id) if callable(getter) else (False, None)
        if isinstance(result, tuple) and len(result) == 2:
            available, record = bool(result[0]), result[1]
        else:
            record, available = result, result is not None
        return self._detail(
            record if isinstance(record, Mapping) else None,
            entity_id=loop_run_id,
            label="Loop run",
            dataset="loop_runs",
            surface_key="loop_run_detail",
            available=available,
            surface=self._loop_run_surface(available),
        )

    def downstream_health(self) -> Dict[str, Any]:
        monitor = self.downstream_health_monitor
        state = (
            monitor.get_state()
            if monitor is not None and callable(getattr(monitor, "get_state", None))
            else {"overall_ok": None, "targets": {}}
        )
        return {
            "read_model": "downstream_health",
            "data": state,
            "meta": {
                "source": "bff_downstream_health_monitor" if monitor is not None else "missing"
            },
        }

    async def replay_downstream_health_dead_letters(
        self,
        *,
        identity: Any,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not bool(getattr(identity, "mfa_verified", False)):
            raise self._error(
                403,
                ErrorCode.FORBIDDEN,
                "Downstream health replay requires MFA",
                "A verified second factor is required to redrive delivery side effects.",
                precondition_failed="mfa_verified",
            )
        approval_ref = str(payload.get("approval_ref") or "").strip()
        reason = str(payload.get("reason") or "").strip()
        if not approval_ref or not reason:
            raise self._error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Replay approval evidence is required",
                "approval_ref and reason are required for an audited DLQ replay.",
                precondition_failed="approval_ref",
            )
        channel = str(payload.get("channel") or "").strip() or None
        allowed_channels = {"telemetry", "incident_open", "incident_resolve"}
        if channel is not None and channel not in allowed_channels:
            raise self._error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Unknown downstream health delivery channel",
                f"channel must be one of {sorted(allowed_channels)}",
                precondition_failed="channel",
            )
        replay = getattr(self.downstream_health_monitor, "replay_dead_letters", None)
        if not callable(replay):
            raise self._error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Downstream health delivery monitor unavailable",
                "The configured BFF instance has no durable DLQ replay adapter.",
                precondition_failed="downstream_health_monitor",
            )
        result = await _resolve(
            replay(
                actor_id=str(getattr(identity, "operator_id", "")),
                approval_ref=approval_ref,
                reason=reason,
                event_id=str(payload.get("event_id") or "").strip() or None,
                channel=channel,
            )
        )
        return {
            "read_model": "downstream_health_delivery_replay",
            "data": result,
            "meta": {"source": "bff_downstream_health_monitor", "authority": "operator"},
        }

    def _ooda_status(self, packets: Sequence[Mapping[str, Any]], snapshot_at: str) -> Dict[str, Any]:
        enabled = self.ooda_routes_enabled()
        source = self.dataset_source("ooda_packets") if enabled else "fail_closed"
        status = "ok" if enabled and source != "missing" else (
            "unavailable" if enabled else "fail_closed"
        )
        open_states = {state for states in _OODA_STAGE_STATUSES.values() for state in states}
        return {
            "enabled": enabled,
            "gate_state": "enabled" if enabled else "fail_closed",
            "open_loop_count": sum(
                1 for packet in packets if str(packet.get("status") or "").lower() in open_states
            ),
            "closed_loop_count": sum(
                1 for packet in packets if str(packet.get("status") or "").lower() == "closed"
            ),
            "failed_loop_count": sum(
                1 for packet in packets if str(packet.get("status") or "").lower() == "failed"
            ),
            "total_packet_count": len(packets),
            "live_capital_side_effects": any(
                (packet.get("act") or {}).get("live_capital_side_effects") is True
                for packet in packets
                if str(packet.get("environment") or "").lower() != "live"
            ),
            "fail_closed_gate_posture": "fail_closed",
            "meta": {"snapshot_at": snapshot_at, "source": source, "status": status},
        }

    def control_room(self) -> Dict[str, Any]:
        snapshot_at = self.utc_now()
        loops_available, loops = self._available_records("list_loop_runs")
        sentinel_available, findings = self._available_records("list_sentinel_findings")
        interventions = self.intervention_records()
        packets = self._call_records(("list_ooda_packets",)) if self.ooda_routes_enabled() else []
        loop_surface = self._loop_run_surface(loops_available)
        sentinel_source = self.dataset_source("sentinel_findings")
        sentinel_surface = self._surface(
            "sentinel_findings", source=sentinel_source, available=sentinel_available
        )
        statuses = {loop_surface["status"], sentinel_surface["status"]}
        control_status = "ok" if statuses == {"ok"} else (
            "unavailable" if statuses == {"unavailable"} else "degraded"
        )
        ooda_status = self._ooda_status(packets, snapshot_at)
        return {
            "loops": {"items": loops, "meta": {"surfaces": {"loop_runs": loop_surface}}},
            "interventions": {"items": interventions},
            "sentinel": {
                "items": findings,
                "meta": {"surfaces": {"sentinel_findings": sentinel_surface}},
            },
            "ooda_status": ooda_status,
            "meta": {
                "snapshot_at": snapshot_at,
                "surfaces": {
                    "control_room": {
                        "status": control_status,
                        "source": "composed_domain_ports" if control_status != "unavailable" else "missing",
                    },
                    "loop_runs": loop_surface,
                    "sentinel_findings": sentinel_surface,
                    "ooda_control_room_status": ooda_status["meta"],
                },
            },
        }


__all__ = ["ControlLoopsService", "default_bff_error", "utc_now_rfc3339"]
