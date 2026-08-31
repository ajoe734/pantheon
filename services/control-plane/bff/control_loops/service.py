"""Control Loops domain service.

The service owns the read-model composition and validation used by the
Control Loops router.  It intentionally has no dependency on ``main.py``:
the BFF composition root supplies its durable read store, command admission
callable, loop-truth adapter, and downstream-health monitor.

The static loop catalog and controller-health projection remain owned by the
reusable ``loop_inventory`` and ``loop_truth`` contracts delivered by
``ACG-LOOP-CONTRACTS-20260828``.
"""
from __future__ import annotations

import inspect
import json
import os
import re
import uuid
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from fastapi import HTTPException

try:
    import loop_truth as default_loop_truth
    from loop_inventory import (
        get_loop_inventory_entry,
        list_loop_inventory_entries,
        loop_inventory_meta,
        truth_label_payload,
    )
    from trade_journey_projection_store import InvalidPageToken, ProjectionReadUnavailable
except ImportError:
    from .. import loop_truth as default_loop_truth  # type: ignore[no-redef]
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
    from models import CommandType, ErrorCode, ObjectType
except ImportError:
    from ..models import CommandType, ErrorCode, ObjectType  # type: ignore[no-redef]


CommandSubmitter = Callable[..., Any]
HealthFindingsProvider = Callable[..., List[Dict[str, Any]]]

_LEGACY_LOOP_RUN_SOURCE = "incident_reconstruction"
_LOOP_RUN_PROJECTION_SCHEMA = "pantheon.loop-run-projection.v1"

_VALID_V5_INTERVENTION_DECISIONS = {"approve", "reject", "defer", "dismiss"}
_VALID_REMEDIATION_ACTIONS = {"resolve", "dismiss", "escalate"}
_SENTINEL_FINDING_KINDS = {
    "hiq_sentinel",
    "risk_breach",
    "strategy_drift",
    "loop_anomaly",
    "persona_health",
}
_SENTINEL_FINDING_STATUSES = {"open", "resolved", "dismissed", "escalated"}
_SENTINEL_FINDING_SEVERITIES = {"critical", "high", "medium", "low"}

_OODA_STAGE_DEFS = (
    ("observe", "Observe", "telemetry/source/search health"),
    ("orient", "Orient", "active signal/persona proposal count"),
    ("decide", "Decide", "pending approvals/interventions"),
    ("act", "Act", "paper runtime / sandbox broker state"),
    ("learn", "Learn", "evolution/postmortem/retrain state"),
)
_OODA_STAGE_STATUSES: Dict[str, Tuple[str, ...]] = {
    "observe": ("open", "observing"),
    "orient": ("oriented",),
    "decide": ("decided",),
    "act": ("acted",),
    "learn": ("evolving",),
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
    """Build the canonical BFF error envelope without importing ``main.py``."""

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


class _NullReadStore:
    """Fail-closed read store used only when the composition root injects none."""

    def dataset_source(self, _dataset: str) -> str:
        return "missing"

    def list_ooda_packets(self, **_kwargs: Any) -> List[Dict[str, Any]]:
        return []

    def get_ooda_packet(self, _packet_id: str) -> Optional[Dict[str, Any]]:
        return None

    def list_v5_interventions(self, **_kwargs: Any) -> List[Dict[str, Any]]:
        return []

    def list_sentinel_findings(self, **_kwargs: Any) -> Tuple[bool, List[Dict[str, Any]]]:
        return False, []

    def get_sentinel_finding(self, _finding_id: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
        return False, None

    def list_loop_runs(self) -> Tuple[bool, List[Dict[str, Any]]]:
        return False, []

    def get_loop_run(self, _loop_run_id: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
        return False, None

    def loop_run_projection_metadata(self) -> Dict[str, Any]:
        return {}

    def trade_journey_projection_reader(self) -> None:
        return None


def _dedupe_nonblank_strings(values: Iterable[Any]) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values:
        clean = str(value or "").strip()
        if clean and clean not in seen:
            result.append(clean)
            seen.add(clean)
    return result


def _claim_path_value(claims: Dict[str, Any], path: str) -> Any:
    current: Any = claims
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _claim_value_as_strings(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part for part in re.split(r"\s*,\s*|\s+", value.strip()) if part]
    if isinstance(value, Mapping):
        for key in ("id", "tenant_id", "tenantId", "value", "name"):
            if value.get(key):
                return [str(value[key]).strip()]
        return []
    if isinstance(value, (list, tuple, set)):
        values: List[Any] = []
        for item in value:
            values.extend(_claim_value_as_strings(item))
        return _dedupe_nonblank_strings(values)
    return [str(value).strip()]


def _identity_claim_strings(identity: Any, paths: Sequence[str]) -> List[str]:
    claims = getattr(identity, "claims", {})
    claims = claims if isinstance(claims, dict) else {}
    values: List[Any] = []
    for path in paths:
        values.extend(_claim_value_as_strings(_claim_path_value(claims, path)))
    return _dedupe_nonblank_strings(values)


async def _resolve(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


class ControlLoopsService:
    """Compose Control Loops reads and typed command receipts."""

    def __init__(
        self,
        *,
        read_store: Optional[Any] = None,
        loop_truth_adapter: Optional[Any] = None,
        downstream_health_monitor: Optional[Any] = None,
        command_submitter: Optional[CommandSubmitter] = None,
        final_command_submitter: Optional[CommandSubmitter] = None,
        health_findings_provider: Optional[HealthFindingsProvider] = None,
        utc_now_fn: Optional[Callable[[], str]] = None,
        bff_error_fn: Optional[Callable[..., Exception]] = None,
        deployed_environment: Optional[str] = None,
    ) -> None:
        self.read_store = read_store or _NullReadStore()
        self.loop_truth = loop_truth_adapter or default_loop_truth
        self.downstream_health_monitor = downstream_health_monitor
        self.command_submitter = command_submitter
        self.final_command_submitter = final_command_submitter or command_submitter
        self.health_findings_provider = health_findings_provider
        self.utc_now = utc_now_fn or utc_now_rfc3339
        self.bff_error = bff_error_fn or default_bff_error
        self.deployed_environment = (
            deployed_environment
            if deployed_environment is not None
            else str(os.environ.get("PANTHEON_ENV", "dev")).strip()
        )
        self._intervention_overlays: Dict[str, Dict[str, Any]] = {}
        self._idempotency_receipts: Dict[str, Tuple[str, Dict[str, Any]]] = {}

    def _error(self, *args: Any, **kwargs: Any) -> Exception:
        return self.bff_error(*args, **kwargs)

    def dataset_source(self, dataset: str) -> str:
        source_fn = getattr(self.read_store, "dataset_source", None)
        if not callable(source_fn):
            return "missing"
        try:
            return str(source_fn(dataset) or "missing")
        except (OSError, TypeError, ValueError):
            return "missing"

    def _surface_status(
        self,
        dataset: str,
        *,
        snapshot_at: Optional[str] = None,
        source: Optional[str] = None,
        has_data: Optional[bool] = None,
        missing_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        snapshot_at = snapshot_at or self.utc_now()
        resolved_source = source or self.dataset_source(dataset)
        surface: Dict[str, Any] = {"status": "ok", "source": resolved_source}
        if resolved_source == "local_snapshot":
            surface.update(
                {
                    "status": "degraded",
                    "note": "Served from local BFF snapshot fallback instead of a backend-owned read store.",
                    "staleness": {"served_from": "local_snapshot", "last_known_at": snapshot_at},
                }
            )
        elif resolved_source == _LEGACY_LOOP_RUN_SOURCE:
            surface.update(
                {
                    "status": "degraded",
                    "projection_mode": "backfill",
                    "accepted_live": False,
                    "note": "Incident-derived loop reconstruction is not canonical lifecycle-projector truth.",
                    "staleness": {"served_from": resolved_source, "last_known_at": snapshot_at},
                }
            )
        elif resolved_source == "missing":
            surface.update(
                {
                    "status": "unavailable",
                    "staleness": {"served_from": "unverifiable", "last_known_at": snapshot_at},
                }
            )
        if has_data is False:
            if surface["status"] == "ok":
                surface["status"] = "unavailable"
            if missing_message:
                surface["message"] = missing_message
            surface.setdefault(
                "staleness",
                {"served_from": "unverifiable", "last_known_at": snapshot_at},
            )
        return surface

    def _meta(
        self,
        dataset: str,
        surface_key: str,
        *,
        snapshot_at: Optional[str] = None,
        total: Optional[int] = None,
        source: Optional[str] = None,
        surface: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        snapshot_at = snapshot_at or self.utc_now()
        resolved_surface = surface or self._surface_status(
            dataset,
            snapshot_at=snapshot_at,
            source=source,
        )
        meta: Dict[str, Any] = {
            "snapshot_at": snapshot_at,
            "surfaces": {surface_key: resolved_surface},
        }
        if total is not None:
            meta["total"] = total
        if resolved_surface.get("status") != "ok":
            meta["degradation"] = {
                "reason": resolved_surface.get("message")
                or resolved_surface.get("note")
                or f"{surface_key.replace('_', ' ')} is currently unavailable."
            }
        return meta

    def list_response(
        self,
        items: Sequence[Dict[str, Any]],
        *,
        dataset: str,
        surface_key: str,
        source: Optional[str] = None,
        surface: Optional[Dict[str, Any]] = None,
        next_page_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        records = [deepcopy(item) for item in items]
        if source == "bff_local_registry":
            meta = {
                "snapshot_at": self.utc_now(),
                "surfaces": {surface_key: {"status": "ok", "source": source}},
                "total": len(records),
            }
        else:
            meta = self._meta(
                dataset,
                surface_key,
                total=len(records),
                source=source,
                surface=surface,
            )
        return {
            "data": records,
            "items": records,
            "page_info": {
                "next_page_token": next_page_token,
                "total": len(records),
            },
            "meta": meta,
        }

    def degraded_detail(
        self,
        *,
        entity_id: str,
        label: str,
        dataset: str,
        surface_key: str,
        source: Optional[str] = None,
    ) -> Dict[str, Any]:
        snapshot_at = self.utc_now()
        surface = self._surface_status(
            dataset,
            snapshot_at=snapshot_at,
            source=source,
            has_data=False,
            missing_message=f"{label} read model source is unavailable.",
        )
        return {
            "data": {
                "id": entity_id,
                "status": "degraded",
                "readSurface": surface,
                "message": f"{label} read model source is unavailable.",
            },
            "meta": self._meta(
                dataset,
                surface_key,
                snapshot_at=snapshot_at,
                surface=surface,
            ),
        }

    def read_model_detail(
        self,
        record: Optional[Dict[str, Any]],
        *,
        entity_id: str,
        label: str,
        dataset: str,
        surface_key: str,
        source: Optional[str] = None,
        source_available: Optional[bool] = None,
        surface: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        resolved_surface = surface or self._surface_status(dataset, source=source)
        if record:
            return {
                "data": deepcopy(record),
                "meta": self._meta(dataset, surface_key, surface=resolved_surface),
            }
        if source_available is False or resolved_surface.get("status") == "unavailable":
            return self.degraded_detail(
                entity_id=entity_id,
                label=label,
                dataset=dataset,
                surface_key=surface_key,
                source=source,
            )
        raise self._error(
            404,
            ErrorCode.RESOURCE_NOT_FOUND,
            f"{label} not found",
            f"{label} {entity_id} does not exist",
        )

    def registry_detail(
        self,
        record: Optional[Dict[str, Any]],
        *,
        entity_id: str,
        label: str,
        surface_key: str,
    ) -> Dict[str, Any]:
        if not record:
            raise self._error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                f"{label} not found",
                f"{label} {entity_id} does not exist",
            )
        return {
            "data": deepcopy(record),
            "meta": {
                "snapshot_at": self.utc_now(),
                "surfaces": {surface_key: {"status": "ok", "source": "bff_local_registry"}},
            },
        }

    @staticmethod
    def _page_slice(
        items: Sequence[Dict[str, Any]],
        page_token: Optional[str],
        page_size: int,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        try:
            start = int(page_token) if page_token else 0
        except (TypeError, ValueError) as exc:
            raise default_bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Invalid page_token",
                "page_token must be a non-negative integer offset",
            ) from exc
        if start < 0:
            raise default_bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Invalid page_token",
                "page_token must be a non-negative integer offset",
            )
        end = start + page_size
        return [deepcopy(item) for item in items[start:end]], str(end) if end < len(items) else None

    @staticmethod
    def ooda_routes_enabled() -> bool:
        raw = os.getenv("PANTHEON_OODA_PACKET_ENABLED")
        if raw is None:
            return True
        return raw.strip().lower() not in {"0", "false", "no", "off", "disabled"}

    def _require_ooda_routes_enabled(self) -> None:
        if self.ooda_routes_enabled():
            return
        raise self._error(
            503,
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "OODA packet read routes disabled",
            "PANTHEON_OODA_PACKET_ENABLED is disabled for this BFF instance.",
            precondition_failed="ooda_packet_feature_flag",
            suggestion="Re-enable the OODA packet read surface before retrying this route.",
        )

    def _call_list(self, name: str, **filters: Any) -> List[Dict[str, Any]]:
        lister = getattr(self.read_store, name, None)
        if not callable(lister):
            return []
        try:
            records = lister(**filters)
        except TypeError:
            records = lister()
            for key, value in filters.items():
                if value is None:
                    continue
                records = [
                    item
                    for item in records
                    if str(item.get(key) or "").lower() == str(value).lower()
                ]
        return [dict(item) for item in records or [] if isinstance(item, Mapping)]

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
        packets = self._call_list(
            "list_ooda_packets",
            status=status,
            stage=stage,
            strategy_id=strategy_id,
            runtime_id=runtime_id,
            evolution_program_id=evolution_program_id,
        )
        page_items, next_token = self._page_slice(packets, page_token, page_size)
        meta = self._meta("ooda_packets", "ooda_packets", total=len(packets))
        return {
            "data": page_items,
            "items": page_items,
            "page_info": {"next_page_token": next_token, "total": len(packets)},
            "meta": meta,
        }

    def get_ooda_packet(self, packet_id: str) -> Dict[str, Any]:
        self._require_ooda_routes_enabled()
        getter = getattr(self.read_store, "get_ooda_packet", None)
        record = getter(packet_id) if callable(getter) else None
        source = self.dataset_source("ooda_packets")
        return self.read_model_detail(
            dict(record) if isinstance(record, Mapping) else None,
            entity_id=packet_id,
            label="OODA packet",
            dataset="ooda_packets",
            surface_key="ooda_packet_detail",
            source=source,
            source_available=False if source == "missing" else None,
        )

    def intervention_records(
        self,
        *,
        status: Optional[str] = None,
        kind: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        records: Dict[str, Dict[str, Any]] = {}
        for record in self._call_list("list_v5_interventions", status=status, kind=kind):
            record_id = str(record.get("intervention_id") or record.get("id") or "").strip()
            if record_id:
                records[record_id] = record
        for record_id, record in self._intervention_overlays.items():
            if status and str(record.get("status") or "") != status:
                continue
            if kind and str(record.get("kind") or "") != kind:
                continue
            records[record_id] = deepcopy(record)
        return list(records.values())

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
        record = next(
            (
                item
                for item in self.intervention_records()
                if str(item.get("intervention_id") or item.get("id") or "") == clean_id
            ),
            None,
        )
        return self.registry_detail(
            record,
            entity_id=clean_id,
            label="Intervention",
            surface_key="intervention_detail",
        )

    def validate_decision(self, decision: str, identity: Any) -> str:
        clean = str(decision or "").strip().lower()
        if clean not in _VALID_V5_INTERVENTION_DECISIONS:
            raise self._error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Invalid intervention decision value",
                f"decision must be one of {sorted(_VALID_V5_INTERVENTION_DECISIONS)}",
                precondition_failed="decision",
            )
        roles = set(getattr(identity, "roles", []) or [])
        if not {"operator", "approver", "admin"}.intersection(roles):
            raise self._error(
                403,
                ErrorCode.FORBIDDEN,
                "DecideV5Intervention requires operator authority",
                "Operator does not hold the required role",
                precondition_failed="role_check",
            )
        return clean

    def validate_remediation(self, payload: Dict[str, Any], identity: Any) -> str:
        action = str(payload.get("remediation_action") or "").strip().lower()
        if action not in _VALID_REMEDIATION_ACTIONS:
            raise self._error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Invalid remediation_action value",
                f"remediation_action must be one of {sorted(_VALID_REMEDIATION_ACTIONS)}",
                precondition_failed="remediation_action",
            )
        roles = set(getattr(identity, "roles", []) or [])
        if not {"approver", "admin"}.intersection(roles):
            raise self._error(
                403,
                ErrorCode.FORBIDDEN,
                "RemediateSentinelIntervention requires approver authority",
                "Operator does not hold the required role",
                precondition_failed="role_check",
            )
        return action

    @staticmethod
    def reject_body_idempotency_key(payload: Dict[str, Any]) -> None:
        if any(key in payload for key in ("idempotencyKey", "idempotency_key")):
            raise default_bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Idempotency key must be supplied in a header",
                "Body idempotency keys are not accepted",
                precondition_failed="idempotency_key_location",
            )

    async def submit_typed_command(
        self,
        *,
        command_type: Any,
        target_type: Any,
        target_id: str,
        payload: Dict[str, Any],
        identity: Any,
        idempotency_key: Optional[str],
        x_idempotency_key: Optional[str],
        action: Optional[str] = None,
        route: Optional[str] = None,
        headers: Optional[Dict[str, Optional[str]]] = None,
        background_tasks: Optional[Any] = None,
        terminal_on_persist: bool = False,
        trusted_evidence_producer: Optional[str] = None,
    ) -> Dict[str, Any]:
        resolved_key = str(idempotency_key or x_idempotency_key or "").strip() or str(uuid.uuid4())
        command_value = getattr(command_type, "value", str(command_type))
        target_value = getattr(target_type, "value", str(target_type))
        fingerprint = json.dumps(
            {
                "command": command_value,
                "target": {"type": target_value, "id": target_id},
                "payload": payload,
                "action": action,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        replay = self._idempotency_receipts.get(resolved_key)
        if replay:
            prior_fingerprint, prior_response = replay
            if prior_fingerprint != fingerprint:
                raise self._error(
                    409,
                    ErrorCode.IDEMPOTENCY_CONFLICT,
                    "Idempotency key already used with a different command",
                    "The supplied idempotency key is bound to another payload",
                    precondition_failed="idempotency_key",
                )
            response = deepcopy(prior_response)
            response.setdefault("meta", {}).setdefault("idempotency", {})["replayed"] = True
            return response

        submitter = self.final_command_submitter if route else self.command_submitter
        if submitter is not None:
            submitted = await _resolve(
                submitter(
                    command_type=command_value,
                    target_type=target_value,
                    target_id=target_id,
                    payload=deepcopy(payload),
                    identity=identity,
                    idempotency_key=resolved_key,
                    action=action,
                    route=route,
                    headers=dict(headers or {}),
                    background_tasks=background_tasks,
                    terminal_on_persist=terminal_on_persist,
                    trusted_evidence_producer=trusted_evidence_producer,
                )
            )
            if isinstance(submitted, Mapping):
                response = deepcopy(dict(submitted))
            else:
                response = {"data": submitted}
        else:
            command_id = f"cmd-{uuid.uuid4().hex[:12]}"
            response = {
                "status": "accepted",
                "data": {
                    "command": command_value,
                    "commandId": command_id,
                    "command_id": command_id,
                    "receipt_id": command_id,
                    "target": {"type": target_value, "id": target_id},
                    "action": action,
                },
                "meta": {
                    "durable": False,
                    "liveCapitalSideEffects": False,
                    "source": "prepared_domain_router",
                },
            }
        meta = response.setdefault("meta", {})
        meta.setdefault("durable", submitter is not None)
        meta.setdefault("liveCapitalSideEffects", False)
        meta["idempotency"] = {"key": resolved_key, "replayed": False}
        self._idempotency_receipts[resolved_key] = (fingerprint, deepcopy(response))
        return response

    def _sentinel_source(self, available: bool) -> Tuple[str, str]:
        if available and self.dataset_source("incidents") == "missing":
            return "sentinel_findings", self.dataset_source("sentinel_findings")
        return "incidents", self.dataset_source("incidents") if available else "missing"

    def list_sentinel_findings(
        self,
        *,
        kind: Optional[str],
        status: Optional[str],
        severity: Optional[str],
        tenant_id: Optional[str],
    ) -> Dict[str, Any]:
        filters = (
            (kind, _SENTINEL_FINDING_KINDS, "kind"),
            (status, _SENTINEL_FINDING_STATUSES, "status"),
            (severity, _SENTINEL_FINDING_SEVERITIES, "severity"),
        )
        for value, allowed, label in filters:
            if value is not None and value.lower() not in allowed:
                raise self._error(
                    400,
                    ErrorCode.VALIDATION_FAILED,
                    f"Invalid {label} '{value}'. Must be one of: {', '.join(sorted(allowed))}",
                    f"Unknown sentinel finding {label} filter value",
                    precondition_failed=label,
                )
        lister = getattr(self.read_store, "list_sentinel_findings", None)
        available, records = (False, [])
        if callable(lister):
            result = lister(kind=kind, status=status, severity=severity)
            if isinstance(result, tuple) and len(result) == 2:
                available, records = bool(result[0]), list(result[1] or [])
            else:
                records = list(result or [])
                available = True
        if self.health_findings_provider:
            derived = self.health_findings_provider(
                kind=kind,
                status=status,
                severity=severity,
                tenant_id=tenant_id,
            )
            existing_ids = {str(record.get("id") or "") for record in records}
            records.extend(
                dict(record)
                for record in derived or []
                if str(record.get("id") or "") not in existing_ids
            )
            available = available or bool(derived)
        dataset, source = self._sentinel_source(available)
        return self.list_response(
            [dict(record) for record in records if isinstance(record, Mapping)],
            dataset=dataset,
            surface_key="sentinel_findings",
            source=source,
        )

    def get_sentinel_finding(self, finding_id: str) -> Dict[str, Any]:
        getter = getattr(self.read_store, "get_sentinel_finding", None)
        available, record = (False, None)
        if callable(getter):
            result = getter(finding_id)
            if isinstance(result, tuple) and len(result) == 2:
                available, record = bool(result[0]), result[1]
            else:
                record = result
                available = record is not None
        dataset, source = self._sentinel_source(available)
        return self.read_model_detail(
            dict(record) if isinstance(record, Mapping) else None,
            entity_id=finding_id,
            label="Sentinel finding",
            dataset=dataset,
            surface_key="sentinel_finding_detail",
            source=source,
            source_available=None if available else False,
        )

    def loop_inventory(self) -> Dict[str, Any]:
        payload = self.list_response(
            list_loop_inventory_entries(),
            dataset="loop_inventory",
            surface_key="loop_inventory",
            source="bff_local_registry",
        )
        return self._loop_inventory_meta(payload)

    def loop_inventory_detail(self, loop_id: str) -> Dict[str, Any]:
        payload = self.registry_detail(
            get_loop_inventory_entry(loop_id),
            entity_id=loop_id,
            label="Loop inventory entry",
            surface_key="loop_inventory",
        )
        return self._loop_inventory_meta(payload)

    @staticmethod
    def _loop_inventory_meta(payload: Dict[str, Any]) -> Dict[str, Any]:
        meta = dict(payload.get("meta") or {})
        surfaces = meta.setdefault("surfaces", {})
        surfaces.setdefault("loop_inventory", {}).update(
            {
                "truth_level": "registry_metadata",
                "registry_ref": "docs/deployment/loop-catalog.registry.json",
                "note": "Static loop catalog read model; live_status is false unless live evidence is present.",
            }
        )
        meta["catalog"] = loop_inventory_meta()
        payload["meta"] = meta
        return payload

    def authenticated_loop_truth_scope(
        self,
        identity: Any,
        *,
        requested_tenant: Optional[str],
        requested_environment: Optional[str],
    ) -> Tuple[str, str]:
        tenant_defaults = _identity_claim_strings(
            identity,
            ("tenant_id", "tenantId", "tenant.id", "tid", "org_id"),
        )
        allowed_tenants = _dedupe_nonblank_strings(
            [
                *_identity_claim_strings(
                    identity,
                    ("allowed_tenants", "allowedTenants", "tenant_ids", "tenantIds", "tenants"),
                ),
                *tenant_defaults,
            ]
        )
        requested_tenant = str(requested_tenant or "").strip()
        default_tenant = next(
            (value for value in tenant_defaults if value != "*"),
            next((value for value in allowed_tenants if value != "*"), ""),
        )
        tenant_id = requested_tenant or default_tenant
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
                details_extra={"tenantId": tenant_id, "allowedTenantIds": allowed_tenants},
            )

        environment = str(requested_environment or "").strip() or self.deployed_environment
        allowed_environments = _identity_claim_strings(
            identity,
            ("environment", "environments", "allowed_environments", "allowedEnvironments"),
        )
        environment_allowed = (
            "*" in allowed_environments
            or environment in allowed_environments
            if allowed_environments
            else environment == self.deployed_environment
        )
        if not environment or not environment_allowed:
            raise self._error(
                403,
                ErrorCode.FORBIDDEN,
                "Environment access denied",
                "Requested controller truth environment is outside the authenticated deployment scope",
                precondition_failed="environment_scope",
                details_extra={
                    "environment": environment,
                    "allowedEnvironments": allowed_environments or [self.deployed_environment],
                },
            )
        return tenant_id, environment

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
        available, health_records = await self.loop_truth.fetch_controller_store_health_records(
            tenant_id,
            environment,
        )
        health_source = "controller_store" if available else "missing"
        records = self.loop_truth.project_canonical_loop_health(
            health_records,
            health_source=health_source,
        )
        payload = self.list_response(
            records,
            dataset="loop_health",
            surface_key="loop_health",
            source="bff_local_registry",
        )
        return self._loop_health_meta(
            payload,
            health_records_available=available,
            health_record_count=len(health_records),
            accepted_count=sum(
                1
                for record in records
                if (record.get("controller_health") or {}).get("current_record_accepted")
            ),
            health_source=health_source,
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
        available, health_records = await self.loop_truth.fetch_controller_store_health_records(
            tenant_id,
            environment,
        )
        health_source = "controller_store" if available else "missing"
        payload = self.registry_detail(
            self.loop_truth.project_canonical_loop_health_entry(
                loop_id,
                health_records,
                health_source=health_source,
            ),
            entity_id=loop_id,
            label="Loop health entry",
            surface_key="loop_health",
        )
        accepted = bool(
            ((payload.get("data") or {}).get("controller_health") or {}).get(
                "current_record_accepted"
            )
        )
        return self._loop_health_meta(
            payload,
            health_records_available=available,
            health_record_count=len(health_records),
            accepted_count=1 if accepted else 0,
            health_source=health_source,
            tenant_id=tenant_id,
            environment=environment,
        )

    def _loop_health_meta(
        self,
        payload: Dict[str, Any],
        *,
        health_records_available: bool,
        health_record_count: int,
        accepted_count: int,
        health_source: str,
        tenant_id: str,
        environment: str,
    ) -> Dict[str, Any]:
        meta = dict(payload.get("meta") or {})
        snapshot_at = str(meta.get("snapshot_at") or self.utc_now())
        raw_items = payload.get("items") or (
            [payload.get("data")] if isinstance(payload.get("data"), dict) else []
        )
        canonical_items = [
            item
            for item in raw_items
            if isinstance(item, dict) and item.get("classification") != "composite_overlay"
        ]
        target_count = len(canonical_items) if canonical_items else len(raw_items)
        if target_count and accepted_count >= target_count:
            health_surface: Dict[str, Any] = {
                "status": "ok",
                "source": "bff_composed",
                "truth_level": "controller_snapshot",
            }
        elif accepted_count:
            health_surface = {
                "status": "degraded",
                "source": "bff_composed",
                "truth_level": "partial_controller_snapshot",
                "staleness": {"served_from": "mixed", "last_known_at": snapshot_at},
            }
        else:
            health_surface = {
                "status": "degraded",
                "source": "bff_composed",
                "truth_level": "registry_metadata",
                "staleness": {"served_from": "registry_only", "last_known_at": snapshot_at},
            }
        catalog = loop_inventory_meta()
        surfaces = meta.setdefault("surfaces", {})
        surfaces["loop_health"] = health_surface
        surfaces["loop_inventory"] = {
            "status": "ok",
            "source": "bff_local_registry",
            "truth_level": "registry_metadata",
            "registry_ref": "docs/deployment/loop-catalog.registry.json",
        }
        surfaces["loop_health_snapshots"] = self._surface_status(
            "loop_health",
            snapshot_at=snapshot_at,
            source=health_source if health_records_available else "missing",
        )
        meta["catalog"] = catalog
        meta["composite_overlay_inventory"] = [
            item
            for item in list_loop_inventory_entries()
            if item.get("classification") == "composite_overlay"
        ]
        meta["truth_labels"] = truth_label_payload()
        meta["coverage"] = {
            "loop_count": len(raw_items),
            "canonical_loop_count": catalog["inventory_counts"]["canonical_loop_count"],
            "composite_overlay_count": catalog["inventory_counts"]["composite_overlay_count"],
            "inventory_entry_count": catalog["inventory_counts"]["inventory_entry_count"],
            "controller_health_record_count": accepted_count,
            "raw_health_record_count": health_record_count,
            "controller_health_records_available": health_records_available,
            "accepted_controller_health_records_available": bool(accepted_count),
        }
        meta["scope"] = {
            "tenant_id": tenant_id,
            "environment": environment,
            "source": "authenticated_identity_and_deployment_scope",
        }
        payload["meta"] = meta
        return payload

    def loop_run_surface_status(
        self,
        available: bool,
        *,
        snapshot_at: Optional[str] = None,
    ) -> Tuple[str, str, Dict[str, Any]]:
        canonical_source = self.dataset_source("loop_runs")
        if canonical_source != "missing":
            dataset, source = "loop_runs", canonical_source
        elif available and self.dataset_source("incidents") != "missing":
            dataset, source = "incidents", _LEGACY_LOOP_RUN_SOURCE
        else:
            dataset, source = "loop_runs", "missing"
        surface = self._surface_status(dataset, snapshot_at=snapshot_at, source=source)
        if dataset != "loop_runs" or source == "missing":
            return dataset, source, surface
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
                "truth_status": "formal" if formal and surface.get("status") == "ok" else "degraded",
            }
        )
        if not formal or surface.get("status") != "ok":
            surface["status"] = "degraded"
        return dataset, source, surface

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
        projection_reader_fn = getattr(self.read_store, "trade_journey_projection_reader", None)
        projection_reader = projection_reader_fn() if callable(projection_reader_fn) else None
        if projection_reader is not None:
            scoped_tenant, scoped_environment = self.authenticated_loop_truth_scope(
                identity,
                requested_tenant=tenant_id,
                requested_environment=environment,
            )
            statuses = sorted(
                {part.strip().lower() for part in (status or "").split(",") if part.strip()}
            )
            try:
                records, next_token = projection_reader.page_loop_runs(
                    tenant_id=scoped_tenant,
                    environment=scoped_environment,
                    statuses=statuses,
                    page_size=page_size,
                    page_token=page_token,
                )
                controller = projection_reader.controller_freshness(
                    tenant_id=scoped_tenant,
                    environment=scoped_environment,
                ) or {}
            except InvalidPageToken as exc:
                raise self._error(
                    422,
                    ErrorCode.VALIDATION_FAILED,
                    "Invalid page_token",
                    "page_token does not match this tenant/environment scope",
                ) from exc
            except ProjectionReadUnavailable:
                return self.list_response(
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
            response = self.list_response(
                records,
                dataset="loop_runs",
                surface_key="loop_runs",
                source="postgres_lifecycle_projection",
                surface=surface,
                next_page_token=next_token,
            )
            response["page_info"].update(
                {
                    "page_size": page_size,
                    "returned": len(records),
                    "has_more": next_token is not None,
                }
            )
            return response

        lister = getattr(self.read_store, "list_loop_runs", None)
        result = lister() if callable(lister) else (False, [])
        if isinstance(result, tuple) and len(result) == 2:
            available, records = bool(result[0]), list(result[1] or [])
        else:
            records = list(result or [])
            available = True
        if status:
            requested = {part.strip().lower() for part in status.split(",") if part.strip()}
            records = [
                record
                for record in records
                if str(record.get("status") or "").lower() in requested
            ]
        dataset, source, surface = self.loop_run_surface_status(available)
        return self.list_response(
            [dict(record) for record in records if isinstance(record, Mapping)],
            dataset=dataset,
            surface_key="loop_runs",
            source=source,
            surface=surface,
        )

    async def get_loop_run(
        self,
        loop_run_id: str,
        identity: Any,
        *,
        tenant_id: Optional[str],
        environment: Optional[str],
    ) -> Dict[str, Any]:
        projection_reader_fn = getattr(self.read_store, "trade_journey_projection_reader", None)
        projection_reader = projection_reader_fn() if callable(projection_reader_fn) else None
        if projection_reader is not None:
            scoped_tenant, scoped_environment = self.authenticated_loop_truth_scope(
                identity,
                requested_tenant=tenant_id,
                requested_environment=environment,
            )
            try:
                record = projection_reader.get_loop_run(
                    tenant_id=scoped_tenant,
                    environment=scoped_environment,
                    loop_run_id=loop_run_id,
                )
                controller = projection_reader.controller_freshness(
                    tenant_id=scoped_tenant,
                    environment=scoped_environment,
                ) or {}
            except ProjectionReadUnavailable:
                return self.degraded_detail(
                    entity_id=loop_run_id,
                    label="Loop run",
                    dataset="loop_runs",
                    surface_key="loop_run_detail",
                    source="missing",
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
            return self.read_model_detail(
                dict(record) if isinstance(record, Mapping) else None,
                entity_id=loop_run_id,
                label="Loop run",
                dataset="loop_runs",
                surface_key="loop_run_detail",
                source="postgres_lifecycle_projection",
                source_available=True,
                surface=surface,
            )
        getter = getattr(self.read_store, "get_loop_run", None)
        result = getter(loop_run_id) if callable(getter) else (False, None)
        if isinstance(result, tuple) and len(result) == 2:
            available, record = bool(result[0]), result[1]
        else:
            record = result
            available = record is not None
        dataset, source, surface = self.loop_run_surface_status(available)
        return self.read_model_detail(
            dict(record) if isinstance(record, Mapping) else None,
            entity_id=loop_run_id,
            label="Loop run",
            dataset=dataset,
            surface_key="loop_run_detail",
            source=source,
            source_available=None if available else False,
            surface=surface,
        )

    def downstream_health(self) -> Dict[str, Any]:
        monitor = self.downstream_health_monitor
        state = monitor.get_state() if monitor is not None and hasattr(monitor, "get_state") else {
            "overall_ok": None,
            "targets": {},
        }
        return {
            "read_model": "downstream_health",
            "data": state,
            "meta": {
                "source": "bff_downstream_health_monitor" if monitor is not None else "missing",
                "description": "Live probe results from the BFF continuous downstream health monitor.",
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
        monitor = self.downstream_health_monitor
        replay = getattr(monitor, "replay_dead_letters", None) if monitor is not None else None
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

    def build_ooda_control_room_status(self, snapshot_at: str) -> Dict[str, Any]:
        if not self.ooda_routes_enabled():
            return {
                "enabled": False,
                "gate_state": "fail_closed",
                "open_loop_count": 0,
                "closed_loop_count": 0,
                "failed_loop_count": 0,
                "total_packet_count": 0,
                "stages": {
                    stage: {
                        "label": label,
                        "description": description,
                        "status": "fail_closed",
                        "active_count": 0,
                        "detail_link": f"/bff/ooda/packets?stage={stage}",
                    }
                    for stage, label, description in _OODA_STAGE_DEFS
                },
                "live_capital_side_effects": False,
                "fail_closed_gate_posture": "fail_closed",
                "meta": {
                    "snapshot_at": snapshot_at,
                    "source": "fail_closed",
                    "status": "fail_closed",
                    "surface_key": "ooda_control_room_status",
                },
            }
        packets = self._call_list("list_ooda_packets")
        source = self.dataset_source("ooda_packets")
        if source == "missing" and packets:
            source = "composed_market_persona_defaults"
        surface_status = "ok" if source != "missing" else "unavailable"
        open_statuses = {"open", "observing", "oriented", "decided", "acted", "evolving"}
        return {
            "enabled": True,
            "gate_state": "enabled",
            "open_loop_count": sum(
                1 for packet in packets if str(packet.get("status") or "").lower() in open_statuses
            ),
            "closed_loop_count": sum(
                1 for packet in packets if str(packet.get("status") or "").lower() == "closed"
            ),
            "failed_loop_count": sum(
                1 for packet in packets if str(packet.get("status") or "").lower() == "failed"
            ),
            "total_packet_count": len(packets),
            "stages": {
                stage: {
                    "label": label,
                    "description": description,
                    "status": surface_status,
                    "active_count": sum(
                        1
                        for packet in packets
                        if str(packet.get("status") or "").lower() in _OODA_STAGE_STATUSES[stage]
                    ),
                    "detail_link": f"/bff/ooda/packets?stage={stage}",
                }
                for stage, label, description in _OODA_STAGE_DEFS
            },
            "live_capital_side_effects": any(
                (packet.get("act") or {}).get("live_capital_side_effects") is True
                for packet in packets
                if str(packet.get("environment") or "").lower() != "live"
            ),
            "fail_closed_gate_posture": "fail_closed",
            "meta": {
                "snapshot_at": snapshot_at,
                "source": source,
                "status": surface_status,
                "surface_key": "ooda_control_room_status",
            },
        }

    def control_room(self) -> Dict[str, Any]:
        snapshot_at = self.utc_now()
        loop_lister = getattr(self.read_store, "list_loop_runs", None)
        loop_result = loop_lister() if callable(loop_lister) else (False, [])
        if isinstance(loop_result, tuple) and len(loop_result) == 2:
            loops_available, loop_runs = bool(loop_result[0]), list(loop_result[1] or [])
        else:
            loop_runs = list(loop_result or [])
            loops_available = True
        sentinel_lister = getattr(self.read_store, "list_sentinel_findings", None)
        sentinel_result = sentinel_lister() if callable(sentinel_lister) else (False, [])
        if isinstance(sentinel_result, tuple) and len(sentinel_result) == 2:
            sentinel_available, findings = bool(sentinel_result[0]), list(sentinel_result[1] or [])
        else:
            findings = list(sentinel_result or [])
            sentinel_available = True
        _, _, loop_surface = self.loop_run_surface_status(
            loops_available, snapshot_at=snapshot_at
        )
        incidents_source = self.dataset_source("incidents")
        sentinel_surface = self._surface_status(
            "incidents" if incidents_source != "missing" else "sentinel_findings",
            snapshot_at=snapshot_at,
            source=incidents_source if incidents_source != "missing" else (
                self.dataset_source("sentinel_findings") if sentinel_available else "missing"
            ),
        )
        statuses = {loop_surface.get("status"), sentinel_surface.get("status")}
        if statuses == {"ok"}:
            control_surface: Dict[str, Any] = {"status": "ok", "source": "composed_read_models"}
        elif statuses == {"unavailable"}:
            control_surface = {
                "status": "unavailable",
                "source": "missing",
                "staleness": {"served_from": "unverifiable", "last_known_at": snapshot_at},
            }
        else:
            control_surface = {
                "status": "degraded",
                "source": "composed_read_models",
                "staleness": {"served_from": "mixed", "last_known_at": snapshot_at},
            }
        ooda = self.build_ooda_control_room_status(snapshot_at)
        return {
            "loops": {
                "items": loop_runs,
                "meta": {"snapshot_at": snapshot_at, "surfaces": {"loop_runs": loop_surface}},
            },
            "interventions": {
                "items": self.intervention_records(),
                "meta": {
                    "snapshot_at": snapshot_at,
                    "surfaces": {
                        "interventions": {"status": "ok", "source": "bff_local_registry"}
                    },
                },
            },
            "sentinel": {
                "items": findings,
                "meta": {
                    "snapshot_at": snapshot_at,
                    "surfaces": {"sentinel_findings": sentinel_surface},
                },
            },
            "ooda_status": ooda,
            "meta": {
                "snapshot_at": snapshot_at,
                "surfaces": {
                    "control_room": control_surface,
                    "loop_runs": loop_surface,
                    "sentinel_findings": sentinel_surface,
                    "ooda_control_room_status": ooda["meta"],
                },
            },
        }


__all__ = [
    "ControlLoopsService",
    "default_bff_error",
    "utc_now_rfc3339",
    "CommandType",
    "ObjectType",
]
