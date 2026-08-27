from __future__ import annotations

import uuid
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional

from fastapi import APIRouter, Body, Header, HTTPException, Query, status

try:
    from services.source_ingestion.connector_definitions import calculate_source_allowed_actions
except ImportError:  # pragma: no cover
    try:
        from connector_definitions import calculate_source_allowed_actions  # type: ignore[no-redef]
    except ImportError:  # pragma: no cover
        def calculate_source_allowed_actions(*args: Any, **kwargs: Any) -> Dict[str, Any]:  # type: ignore[misc]
            return {"canValidate": True, "canCanary": True, "canEnable": True, "canDisable": True, "canDegrade": True, "canResume": True, "canChangeSchedule": True, "canReplace": True, "canRetire": True, "blockedReasons": []}

try:
    from source_management_client import SourceManagementClient, SourceManagementClientError
except ImportError:  # pragma: no cover
    from services.control_plane.bff.source_management_client import (  # type: ignore[no-redef]
        SourceManagementClient,
        SourceManagementClientError,
    )
from .contracts import (
    ActionCommandRequest,
    ChangeScheduleRequest,
    CreateDataSourceRequest,
    DataSourceCatalogEnvelope,
    DataSourceDetailEnvelope,
    DataSourceReceiptsEnvelope,
    DataSourceRunsEnvelope,
    DataSourcesEnvelope,
    ReplaceDataSourceRequest,
    RetireDataSourceRequest,
    SourceCommandReceiptEnvelope,
)


_SECRET_KEYWORDS = frozenset({
    "api_key",
    "apikey",
    "secret",
    "password",
    "token",
    "auth_token",
    "private_key",
    "secret_key",
    "secret_value",
})


def redact_sensitive_values(obj: Any) -> Any:
    """Recursively redact any secret material in responses/logs."""
    if isinstance(obj, dict):
        res: Dict[str, Any] = {}
        for k, v in obj.items():
            k_lower = str(k).lower()
            if any(kw in k_lower for kw in _SECRET_KEYWORDS) and isinstance(v, str):
                if not (v.startswith("env://") or v.startswith("vault://") or v.startswith("ref://") or v == ""):
                    res[k] = "[REDACTED]"
                else:
                    res[k] = v
            else:
                res[k] = redact_sensitive_values(v)
        return res
    elif isinstance(obj, list):
        return [redact_sensitive_values(item) for item in obj]
    elif isinstance(obj, tuple):
        return tuple(redact_sensitive_values(item) for item in obj)
    return obj


def assert_no_raw_secrets(payload: Mapping[str, Any], path: str = "") -> None:
    """Check that no inline secret values are present."""
    for k, v in payload.items():
        curr_path = f"{path}.{k}" if path else str(k)
        lower_k = str(k).lower()
        if any(kw in lower_k for kw in _SECRET_KEYWORDS):
            if isinstance(v, str) and not (v.startswith("env://") or v.startswith("vault://") or v.startswith("ref://") or v == ""):
                raise ValueError(
                    f"Raw secret material detected at {curr_path}: inline secrets are strictly forbidden; use secret_ref_id"
                )
        if isinstance(v, Mapping):
            assert_no_raw_secrets(v, curr_path)


def _default_require_operator_role(identity: Any) -> None:
    roles = set()
    if isinstance(identity, dict):
        roles = set(identity.get("roles") or [])
        actor_type = str(identity.get("actor_type") or "")
        if actor_type in ("operator", "admin", "service", "system", "controller"):
            return
    elif hasattr(identity, "roles"):
        roles = set(identity.roles or [])
    elif hasattr(identity, "role"):
        roles = {str(identity.role)}

    allowed = {"operator", "admin", "service", "system", "controller"}
    if not (roles & allowed):
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "code": "FORBIDDEN",
                    "message": "Operator or Admin role required for data source management mutations",
                    "details": {"reason": "insufficient_role", "roles": list(roles)},
                }
            },
        )


def _default_bff_error(
    status_code: int,
    code: Any,
    message: str,
    reason: str,
    details_extra: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> HTTPException:
    code_str = code.value if hasattr(code, "value") else str(code)
    detail: Dict[str, Any] = {
        "error": {
            "code": code_str,
            "message": message,
            "details": {
                "reason": reason,
                **(details_extra or {}),
            },
        }
    }
    return HTTPException(status_code=status_code, detail=detail)


def create_datasources_router(
    *,
    get_read_store: Callable,
    extract_identity: Callable,
    require_read_role: Callable,
    snapshot_meta: Callable,
    utc_now: Callable,
    read_source_connector_registry: Optional[Callable[[Any], Awaitable[Dict[str, Any]]]] = None,
    get_source_management_client: Optional[Callable[[], SourceManagementClient]] = None,
    require_operator_role: Optional[Callable[[Any], None]] = None,
    bff_error: Optional[Callable[..., HTTPException]] = None,
) -> APIRouter:
    """Factory for BFF data-source management routes (SD-SRCM-03)."""
    router = APIRouter()

    _client_getter = get_source_management_client or (lambda: SourceManagementClient())
    _operator_role_check = require_operator_role or _default_require_operator_role
    _error_handler = bff_error or _default_bff_error

    def _map_client_error(exc: SourceManagementClientError) -> HTTPException:
        status_code = exc.status_code if exc.status_code in {400, 403, 404, 409, 412, 503} else (503 if exc.status_code in {0, 504} else 500)
        code = "INTERNAL_ERROR"
        reason = "upstream_error"
        details_extra: Dict[str, Any] = {}

        if status_code == 404:
            code = "RESOURCE_NOT_FOUND"
            reason = "resource_not_found"
        elif status_code == 409:
            if "STALE_REVISION" in exc.message or "STALE_REVISION" in exc.error_code:
                code = "STALE_REVISION"
                reason = "stale_revision"
            else:
                code = "RESOURCE_CONFLICT"
                reason = "resource_conflict"
        elif status_code == 412:
            code = "PRECONDITION_FAILED"
            reason = "precondition_failed"
        elif status_code == 403:
            code = "FORBIDDEN"
            reason = "forbidden"
        elif status_code == 400:
            if exc.payload.get("detail", {}).get("code") == "adapter_not_supported" or "adapter_not_supported" in exc.message:
                code = "VALIDATION_FAILED"
                reason = "adapter_not_supported"
                dev_need = exc.payload.get("detail", {}).get("development_need")
                if dev_need:
                    details_extra["development_need"] = dev_need
            else:
                code = "VALIDATION_FAILED"
                reason = "invalid_argument"
        elif status_code == 503:
            code = "DEPENDENCY_UNAVAILABLE"
            reason = "dependency_unavailable"

        return _error_handler(
            status_code=status_code,
            code=code,
            message=exc.message,
            reason=reason,
            details_extra=details_extra,
        )

    # -------------------------------------------------------------------------
    # READ ROUTES (SD-SRCM-03 §5.2)
    # -------------------------------------------------------------------------

    @router.get(
        "/bff/management/data-sources",
        response_model=DataSourcesEnvelope,
        response_model_exclude={"items"},
    )
    async def bff_management_data_sources(
        page_token: Optional[str] = Query(default=None),
        page_size: int = Query(default=50, ge=1, le=200),
        source_kind: Optional[str] = Query(default=None),
        lifecycle_state: Optional[str] = Query(default=None),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF BFFGAP-DATASOURCES: Canonical Data-source registry and instances.

        Returns v2 rows when available, and an explicit degraded legacy projection
        during migration when v2 is empty or unconfigured.
        """
        identity = extract_identity(authorization)
        require_read_role(identity)

        snapshot_at = utc_now()
        client = _client_getter()

        v2_sources: Optional[List[Dict[str, Any]]] = None
        definitions_map: Dict[str, Dict[str, Any]] = {}
        client_error = None

        if client.configured:
            try:
                sources_resp = client.list_sources(source_kind=source_kind, lifecycle_state=lifecycle_state)
                v2_sources = list(sources_resp.get("sources") or [])
                def_resp = client.list_connector_definitions()
                for d in def_resp.get("definitions") or []:
                    if isinstance(d, dict) and d.get("definition_id"):
                        definitions_map[d["definition_id"]] = d
            except SourceManagementClientError as exc:
                client_error = exc
                v2_sources = None

        # If v2 sources are available
        if v2_sources is not None and (len(v2_sources) > 0 or client_error is None):
            composed_items: List[Dict[str, Any]] = []
            for inst in v2_sources:
                inst_id = str(inst.get("data_source_id") or inst.get("source_instance_id") or "")
                def_id = str(inst.get("definition_id") or "")
                defn = definitions_map.get(def_id, {
                    "definition_id": def_id,
                    "provider": inst.get("provider", "unknown"),
                    "definition_state": "supported",
                    "deployment_sha": "sha256-current",
                })

                # Fetch desired & observed for this instance
                try:
                    detail_resp = client.get_source(inst_id)
                    desired = detail_resp.get("desired") or {}
                    observed = detail_resp.get("observed") or {}
                except Exception:
                    desired = {"desired_lifecycle": inst.get("lifecycle_state", "configured_disabled"), "revision": inst.get("revision", 1)}
                    observed = {"effective_lifecycle": inst.get("lifecycle_state", "configured_disabled"), "validation_state": "pending", "canary_state": "not_run", "health_state": "healthy"}

                allowed_actions = calculate_source_allowed_actions(defn, inst, desired, observed)
                dto = {
                    "schema_version": "management_data_source.v2",
                    "source_instance_id": inst_id,
                    "connector_id": inst.get("connector_id") or inst_id,
                    "provider": inst.get("provider"),
                    "source_class": inst.get("source_class"),
                    "definition": defn,
                    "instance": inst,
                    "desired": desired,
                    "observed": observed,
                    "allowed_actions": allowed_actions,
                    "allowedActions": allowed_actions,
                    "lineage_summary": {
                        "datasets": inst.get("datasets") or [],
                        "markets": inst.get("markets") or [],
                    },
                }
                composed_items.append(redact_sensitive_values(dto))

            start = 0
            if page_token:
                try:
                    start = int(page_token)
                except (TypeError, ValueError):
                    start = 0
            page_items = composed_items[start : start + page_size]
            next_page_token = str(start + page_size) if start + page_size < len(composed_items) else None

            surface = {
                "status": "ok",
                "source": "service_client",
            }
            meta: Dict[str, Any] = {
                **snapshot_meta(snapshot_at),
                "status": "ok",
                "source": "service_client",
                "surfaces": {
                    "data_sources": surface,
                },
            }
            return {
                "data": {
                    "id": "management-data-sources",
                    "items": page_items,
                    "summary": {
                        "total_items": len(composed_items),
                        "returned_items": len(page_items),
                        "status": "ok",
                        "source": "service_client",
                    },
                    "status": "ok",
                    "source": "service_client",
                },
                "page_info": {
                    "next_page_token": next_page_token,
                    "total": len(composed_items),
                    "page_size": page_size,
                    "returned": len(page_items),
                    "has_more": next_page_token is not None,
                },
                "meta": meta,
            }

        # Fallback to legacy read store registry during migration
        store = get_read_store()
        if read_source_connector_registry is None:
            registry = store.get_source_connector_registry()
        else:
            registry = await read_source_connector_registry(store)

        source: str = str(registry.get("source") or "missing")
        items: List[Dict[str, Any]] = list(registry.get("connectors") or [])

        if source in ("missing", "unavailable"):
            surface_state = "unavailable"
        else:
            surface_state = "ok" if items else "degraded"

        surface = {
            "status": surface_state,
            "source": source,
        }
        if registry.get("reason"):
            surface["reason"] = registry["reason"]
        if surface_state == "unavailable":
            surface["message"] = "Source-ingest registry is unavailable or unconfigured."
            surface["staleness"] = {
                "served_from": "unverifiable" if source == "missing" else source,
                "last_known_at": snapshot_at,
            }
        elif surface_state == "degraded":
            surface["message"] = "Source-ingest registry is readable but currently empty."

        meta = {
            **snapshot_meta(snapshot_at),
            "status": surface_state,
            "source": source,
            "surfaces": {
                "data_sources": surface,
            },
        }
        if surface_state == "unavailable":
            meta["degradation"] = {
                "reason": registry.get("reason") or "management data sources are currently unavailable.",
            }

        start = 0
        if page_token:
            try:
                start = int(page_token)
            except (TypeError, ValueError):
                start = 0
        page_items = items[start : start + page_size]
        next_page_token = str(start + page_size) if start + page_size < len(items) else None

        if source in ("missing", "unavailable"):
            return {
                "data": {
                    "id": "management-data-sources",
                    "items": [],
                    "summary": {
                        "total_items": 0,
                        "returned_items": 0,
                        "status": "unavailable",
                        "source": source,
                    },
                    "status": "unavailable",
                    "source": source,
                },
                "page_info": {
                    "next_page_token": None,
                    "total": 0,
                    "page_size": page_size,
                    "returned": 0,
                    "has_more": False,
                },
                "meta": meta,
            }

        data: Dict[str, Any] = {
            "id": "management-data-sources",
            "items": page_items,
            "summary": {
                "total_items": len(items),
                "returned_items": len(page_items),
                "status": surface_state,
                "source": source,
            },
            "status": surface_state,
            "source": source,
        }
        if registry.get("policy_registry") is not None:
            data["policy_registry"] = registry["policy_registry"]
        if registry.get("financial_data_source_catalog") is not None:
            data["financial_data_source_catalog"] = registry["financial_data_source_catalog"]
        if registry.get("active_universe_policy") is not None:
            data["active_universe_policy"] = registry["active_universe_policy"]
        provider_examples = list(registry.get("provider_examples") or [])
        if provider_examples:
            data["provider_examples"] = provider_examples

        return {
            "data": data,
            "page_info": {
                "next_page_token": next_page_token,
                "total": len(items),
                "page_size": page_size,
                "returned": len(page_items),
                "has_more": next_page_token is not None,
            },
            "meta": meta,
        }

    @router.get(
        "/bff/management/data-sources/catalog",
        response_model=DataSourceCatalogEnvelope,
    )
    async def bff_management_data_sources_catalog(
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """GET /bff/management/data-sources/catalog: Deployed connector definitions catalog."""
        identity = extract_identity(authorization)
        require_read_role(identity)

        snapshot_at = utc_now()
        client = _client_getter()
        store = get_read_store()

        try:
            defs_resp = client.list_connector_definitions()
            definitions = list(defs_resp.get("definitions") or [])
            source_state = "ok"
            source_name = "service_client"
        except SourceManagementClientError as exc:
            definitions = []
            source_state = "unavailable"
            source_name = "unavailable"

        # Also grab policy registry & financial catalog from read_store if available
        policy_registry = None
        financial_catalog = None
        try:
            reg = store.get_source_connector_registry()
            policy_registry = reg.get("policy_registry")
            financial_catalog = reg.get("financial_data_source_catalog")
        except Exception:
            pass

        data = {
            "id": "data-sources-catalog",
            "definitions": redact_sensitive_values(definitions),
            "count": len(definitions),
            "status": source_state,
            "source": source_name,
            "policy_registry": policy_registry,
            "financial_data_source_catalog": financial_catalog,
        }

        meta = {
            **snapshot_meta(snapshot_at),
            "status": source_state,
            "source": source_name,
            "surfaces": {
                "data_source_catalog": {
                    "status": source_state,
                    "source": source_name,
                }
            },
        }
        if source_state == "unavailable":
            meta["degradation"] = {"reason": "Connector definitions catalog is currently unavailable."}

        return {"data": data, "meta": meta}

    @router.get(
        "/bff/management/data-sources/{source_instance_id}",
        response_model=DataSourceDetailEnvelope,
    )
    async def bff_management_data_source_detail(
        source_instance_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """GET /bff/management/data-sources/{source_instance_id}: Single source instance detail DTO."""
        identity = extract_identity(authorization)
        require_read_role(identity)

        snapshot_at = utc_now()
        client = _client_getter()

        try:
            source_resp = client.get_source(source_instance_id)
        except SourceManagementClientError as exc:
            raise _map_client_error(exc)

        instance = source_resp.get("source") or {}
        desired = source_resp.get("desired") or {}
        observed = source_resp.get("observed") or {}

        def_id = str(desired.get("definition_id") or instance.get("definition_id") or "")
        defn: Dict[str, Any] = {}
        if def_id:
            try:
                def_resp = client.get_connector_definition(def_id)
                defn = def_resp.get("definition") or {}
            except Exception:
                defn = {
                    "definition_id": def_id,
                    "provider": instance.get("provider", "unknown"),
                    "definition_state": "supported",
                    "deployment_sha": "sha256-current",
                }

        allowed_actions = calculate_source_allowed_actions(defn, instance, desired, observed)
        data = {
            "id": f"source-detail-{source_instance_id}",
            "source_instance_id": source_instance_id,
            "definition": redact_sensitive_values(defn),
            "instance": redact_sensitive_values(instance),
            "desired": redact_sensitive_values(desired),
            "observed": redact_sensitive_values(observed),
            "allowed_actions": allowed_actions,
            "allowedActions": allowed_actions,
            "lineage_summary": {
                "datasets": instance.get("datasets") or [],
                "markets": instance.get("markets") or [],
                "universe_policy_ref": desired.get("universe_policy_ref") or instance.get("universe_policy_ref"),
            },
            "status": "ok",
            "source": "service_client",
        }

        meta = {
            **snapshot_meta(snapshot_at),
            "status": "ok",
            "source": "service_client",
            "surfaces": {
                "data_source_detail": {
                    "status": "ok",
                    "source": "service_client",
                }
            },
        }
        return {"data": data, "meta": meta}

    @router.get(
        "/bff/management/data-sources/{source_instance_id}/runs",
        response_model=DataSourceRunsEnvelope,
    )
    async def bff_management_data_source_runs(
        source_instance_id: str,
        limit: int = Query(default=50, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """GET /bff/management/data-sources/{source_instance_id}/runs: Recent runs and canaries."""
        identity = extract_identity(authorization)
        require_read_role(identity)

        snapshot_at = utc_now()
        client = _client_getter()

        try:
            obs_resp = client.list_source_observations(source_instance_id, limit=limit)
            observations = list(obs_resp.get("observations") or [])
        except SourceManagementClientError as exc:
            raise _map_client_error(exc)

        try:
            can_resp = client.list_source_canaries(source_instance_id, limit=limit)
            canaries = list(can_resp.get("canaries") or [])
        except SourceManagementClientError as exc:
            canaries = []

        data = {
            "id": f"source-runs-{source_instance_id}",
            "source_instance_id": source_instance_id,
            "observations": redact_sensitive_values(observations),
            "canaries": redact_sensitive_values(canaries),
            "status": "ok",
            "source": "service_client",
        }
        meta = {
            **snapshot_meta(snapshot_at),
            "status": "ok",
            "source": "service_client",
            "surfaces": {
                "data_source_runs": {
                    "status": "ok",
                    "source": "service_client",
                }
            },
        }
        return {"data": data, "meta": meta}

    @router.get(
        "/bff/management/data-sources/{source_instance_id}/receipts",
        response_model=DataSourceReceiptsEnvelope,
    )
    async def bff_management_data_source_receipts(
        source_instance_id: str,
        limit: int = Query(default=50, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """GET /bff/management/data-sources/{source_instance_id}/receipts: Immutable command receipts."""
        identity = extract_identity(authorization)
        require_read_role(identity)

        snapshot_at = utc_now()
        client = _client_getter()

        try:
            resp = client.list_source_receipts(source_instance_id, limit=limit)
            receipts = list(resp.get("receipts") or [])
        except SourceManagementClientError as exc:
            raise _map_client_error(exc)

        data = {
            "id": f"source-receipts-{source_instance_id}",
            "source_instance_id": source_instance_id,
            "receipts": redact_sensitive_values(receipts),
            "count": len(receipts),
            "status": "ok",
            "source": "service_client",
        }
        meta = {
            **snapshot_meta(snapshot_at),
            "status": "ok",
            "source": "service_client",
            "surfaces": {
                "data_source_receipts": {
                    "status": "ok",
                    "source": "service_client",
                }
            },
        }
        return {"data": data, "meta": meta}

    @router.get(
        "/bff/management/source-commands/{receipt_id}",
        response_model=SourceCommandReceiptEnvelope,
    )
    async def bff_management_source_command_receipt(
        receipt_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """GET /bff/management/source-commands/{receipt_id}: Command receipt status."""
        identity = extract_identity(authorization)
        require_read_role(identity)

        snapshot_at = utc_now()
        client = _client_getter()

        try:
            resp = client.get_command_receipt(receipt_id)
            receipt = resp.get("receipt") or resp
        except SourceManagementClientError as exc:
            raise _map_client_error(exc)

        data = {
            "id": f"command-receipt-{receipt_id}",
            "receipt_id": receipt_id,
            "receipt": redact_sensitive_values(receipt),
            "status": "ok",
            "source": "service_client",
        }
        meta = {
            **snapshot_meta(snapshot_at),
            "status": "ok",
            "source": "service_client",
            "surfaces": {
                "source_commands": {
                    "status": "ok",
                    "source": "service_client",
                }
            },
        }
        return {"data": data, "meta": meta}

    # -------------------------------------------------------------------------
    # COMMAND HELPER & WRITE ROUTES (SD-SRCM-03 §5.2 & §5.3)
    # -------------------------------------------------------------------------

    def _execute_bff_source_command(
        *,
        command_type: str,
        source_instance_id: str,
        expected_revision: Optional[int],
        reason: str,
        confirmation: Optional[bool] = False,
        parameters: Dict[str, Any],
        trace_id: Optional[str] = None,
        idempotency_key: Optional[str],
        authorization: Optional[str],
    ) -> Dict[str, Any]:
        # 1. RBAC
        identity = extract_identity(authorization)
        _operator_role_check(identity)

        # 2. Idempotency key check
        clean_idempotency_key = str(idempotency_key or "").strip()
        if not clean_idempotency_key:
            raise _error_handler(
                status_code=400,
                code="VALIDATION_FAILED",
                message="X-Idempotency-Key header is required for source management commands",
                reason="missing_idempotency_key",
            )

        # 3. Reason check
        clean_reason = str(reason or "").strip()
        if not clean_reason:
            raise _error_handler(
                status_code=400,
                code="VALIDATION_FAILED",
                message="reason must not be empty",
                reason="missing_reason",
            )

        # 4. Confirmation check for enable, replace, retire
        if command_type in ("enable", "replace", "retire") and confirmation is not True:
            raise _error_handler(
                status_code=412,
                code="PRECONDITION_FAILED",
                message=f"Explicit confirmation is required for command '{command_type}'",
                reason="confirmation_required",
            )

        # 5. Expected revision check
        if command_type != "create":
            if expected_revision is None or expected_revision < 1:
                raise _error_handler(
                    status_code=400,
                    code="VALIDATION_FAILED",
                    message="expectedRevision is required and must be >= 1",
                    reason="missing_expected_revision",
                )

        # 6. Inline secret check
        try:
            assert_no_raw_secrets(parameters)
        except ValueError as exc:
            raise _error_handler(
                status_code=400,
                code="VALIDATION_FAILED",
                message=str(exc),
                reason="raw_secret_forbidden",
            )

        # 7. Check allowedActions & stale revision on instance before executing
        client = _client_getter()
        if not client.configured:
            raise _error_handler(
                status_code=503,
                code="DEPENDENCY_UNAVAILABLE",
                message="Source ingest service is not configured",
                reason="service_not_configured",
            )

        if command_type != "create":
            try:
                source_data = client.get_source(source_instance_id)
            except SourceManagementClientError as exc:
                raise _map_client_error(exc)

            inst = source_data.get("source") or {}
            des = source_data.get("desired") or {}
            obs = source_data.get("observed") or {}
            curr_rev = inst.get("revision") or des.get("revision") or 1
            if expected_revision is not None and expected_revision != curr_rev:
                raise _error_handler(
                    status_code=409,
                    code="STALE_REVISION",
                    message=f"STALE_REVISION: Expected revision {expected_revision} != current revision {curr_rev}",
                    reason="stale_revision",
                )

            def_id = str(des.get("definition_id") or inst.get("definition_id") or "")
            defn: Dict[str, Any] = {}
            if def_id:
                try:
                    def_resp = client.get_connector_definition(def_id)
                    defn = def_resp.get("definition") or {}
                except Exception:
                    pass

            actions = calculate_source_allowed_actions(defn, inst, des, obs)
            action_map = {
                "validate": "canValidate",
                "canary": "canCanary",
                "enable": "canEnable",
                "disable": "canDisable",
                "degrade": "canDegrade",
                "resume": "canResume",
                "change_schedule": "canChangeSchedule",
                "replace": "canReplace",
                "retire": "canRetire",
            }
            flag = action_map.get(command_type)
            if flag and not actions.get(flag):
                reasons = ", ".join(actions.get("blockedReasons") or []) or "action not permitted by current lifecycle"
                raise _error_handler(
                    status_code=412,
                    code="PRECONDITION_FAILED",
                    message=f"PRECONDITION_FAILED: Command '{command_type}' is not allowed for source {source_instance_id}: {reasons}",
                    reason="action_not_allowed",
                    details_extra={"blocked_reasons": actions.get("blockedReasons") or []},
                )

        # 8. Dispatch command to source-ingest service with service authentication
        actor_id = str(
            getattr(identity, "operator_id", None)
            or getattr(identity, "user_id", None)
            or (identity.get("actor_id") if isinstance(identity, dict) else "operator")
        )
        actor_roles = list(
            getattr(identity, "roles", None)
            or (identity.get("roles") if isinstance(identity, dict) else ["operator"])
        )

        cmd_payload = {
            "command_id": f"srcmd-{uuid.uuid4().hex[:12]}",
            "idempotency_key": clean_idempotency_key,
            "command_type": command_type,
            "source_instance_id": source_instance_id,
            "expected_revision": expected_revision,
            "actor": {
                "actor_id": actor_id,
                "actor_type": "operator",
                "roles": actor_roles,
            },
            "reason": clean_reason,
            "parameters": parameters,
            "trace_id": trace_id,
        }

        try:
            res = client.execute_command(cmd_payload, idempotency_key=clean_idempotency_key)
        except SourceManagementClientError as exc:
            raise _map_client_error(exc)

        receipt = res.get("receipt") or res
        receipt_redacted = redact_sensitive_values(receipt)
        receipt_id = receipt_redacted.get("receipt_id") or "unknown"
        snapshot_at = utc_now()

        return {
            "data": {
                "id": f"command-receipt-{receipt_id}",
                "receipt_id": receipt_id,
                "receipt": receipt_redacted,
                "status": "ok",
                "source": "service_client",
            },
            "meta": {
                **snapshot_meta(snapshot_at),
                "status": "ok",
                "source": "service_client",
                "surfaces": {
                    "source_commands": {
                        "status": "ok",
                        "source": "service_client",
                    }
                },
            },
        }

    @router.post(
        "/bff/management/data-sources",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=SourceCommandReceiptEnvelope,
    )
    async def bff_create_data_source(
        body: CreateDataSourceRequest = Body(...),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """POST /bff/management/data-sources: Create a new configured_disabled data source."""
        params = body.model_dump(by_alias=False, exclude_none=True)
        source_instance_id = str(params.pop("source_instance_id"))
        reason = str(params.pop("reason", "Operator create data source"))
        trace_id = params.pop("trace_id", None)

        return _execute_bff_source_command(
            command_type="create",
            source_instance_id=source_instance_id,
            expected_revision=None,
            reason=reason,
            parameters=params,
            trace_id=trace_id,
            idempotency_key=x_idempotency_key,
            authorization=authorization,
        )

    @router.post(
        "/bff/management/data-sources/{source_instance_id}/actions/validate",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=SourceCommandReceiptEnvelope,
    )
    async def bff_validate_data_source(
        source_instance_id: str,
        body: ActionCommandRequest = Body(...),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """POST /bff/management/data-sources/{source_instance_id}/actions/validate"""
        return _execute_bff_source_command(
            command_type="validate",
            source_instance_id=source_instance_id,
            expected_revision=body.expected_revision,
            reason=body.reason,
            parameters=body.parameters or {},
            trace_id=body.trace_id,
            idempotency_key=x_idempotency_key,
            authorization=authorization,
        )

    @router.post(
        "/bff/management/data-sources/{source_instance_id}/actions/canary",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=SourceCommandReceiptEnvelope,
    )
    async def bff_canary_data_source(
        source_instance_id: str,
        body: ActionCommandRequest = Body(...),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """POST /bff/management/data-sources/{source_instance_id}/actions/canary"""
        return _execute_bff_source_command(
            command_type="canary",
            source_instance_id=source_instance_id,
            expected_revision=body.expected_revision,
            reason=body.reason,
            parameters=body.parameters or {},
            trace_id=body.trace_id,
            idempotency_key=x_idempotency_key,
            authorization=authorization,
        )

    @router.post(
        "/bff/management/data-sources/{source_instance_id}/actions/enable",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=SourceCommandReceiptEnvelope,
    )
    async def bff_enable_data_source(
        source_instance_id: str,
        body: ActionCommandRequest = Body(...),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """POST /bff/management/data-sources/{source_instance_id}/actions/enable"""
        return _execute_bff_source_command(
            command_type="enable",
            source_instance_id=source_instance_id,
            expected_revision=body.expected_revision,
            reason=body.reason,
            confirmation=body.confirmation,
            parameters=body.parameters or {},
            trace_id=body.trace_id,
            idempotency_key=x_idempotency_key,
            authorization=authorization,
        )

    @router.post(
        "/bff/management/data-sources/{source_instance_id}/actions/disable",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=SourceCommandReceiptEnvelope,
    )
    async def bff_disable_data_source(
        source_instance_id: str,
        body: ActionCommandRequest = Body(...),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """POST /bff/management/data-sources/{source_instance_id}/actions/disable"""
        return _execute_bff_source_command(
            command_type="disable",
            source_instance_id=source_instance_id,
            expected_revision=body.expected_revision,
            reason=body.reason,
            parameters=body.parameters or {},
            trace_id=body.trace_id,
            idempotency_key=x_idempotency_key,
            authorization=authorization,
        )

    @router.post(
        "/bff/management/data-sources/{source_instance_id}/actions/degrade",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=SourceCommandReceiptEnvelope,
    )
    async def bff_degrade_data_source(
        source_instance_id: str,
        body: ActionCommandRequest = Body(...),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """POST /bff/management/data-sources/{source_instance_id}/actions/degrade"""
        return _execute_bff_source_command(
            command_type="degrade",
            source_instance_id=source_instance_id,
            expected_revision=body.expected_revision,
            reason=body.reason,
            parameters=body.parameters or {},
            trace_id=body.trace_id,
            idempotency_key=x_idempotency_key,
            authorization=authorization,
        )

    @router.post(
        "/bff/management/data-sources/{source_instance_id}/actions/resume",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=SourceCommandReceiptEnvelope,
    )
    async def bff_resume_data_source(
        source_instance_id: str,
        body: ActionCommandRequest = Body(...),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """POST /bff/management/data-sources/{source_instance_id}/actions/resume"""
        return _execute_bff_source_command(
            command_type="resume",
            source_instance_id=source_instance_id,
            expected_revision=body.expected_revision,
            reason=body.reason,
            parameters=body.parameters or {},
            trace_id=body.trace_id,
            idempotency_key=x_idempotency_key,
            authorization=authorization,
        )

    @router.put(
        "/bff/management/data-sources/{source_instance_id}/schedule",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=SourceCommandReceiptEnvelope,
    )
    async def bff_change_schedule_data_source(
        source_instance_id: str,
        body: ChangeScheduleRequest = Body(...),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """PUT /bff/management/data-sources/{source_instance_id}/schedule"""
        return _execute_bff_source_command(
            command_type="change_schedule",
            source_instance_id=source_instance_id,
            expected_revision=body.expected_revision,
            reason=body.reason,
            parameters={"schedule": body.schedule},
            trace_id=body.trace_id,
            idempotency_key=x_idempotency_key,
            authorization=authorization,
        )

    @router.post(
        "/bff/management/data-sources/{source_instance_id}/actions/replace",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=SourceCommandReceiptEnvelope,
    )
    async def bff_replace_data_source(
        source_instance_id: str,
        body: ReplaceDataSourceRequest = Body(...),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """POST /bff/management/data-sources/{source_instance_id}/actions/replace"""
        return _execute_bff_source_command(
            command_type="replace",
            source_instance_id=source_instance_id,
            expected_revision=body.expected_revision,
            reason=body.reason,
            confirmation=body.confirmation,
            parameters={"replacement_source_id": body.replacement_source_id},
            trace_id=body.trace_id,
            idempotency_key=x_idempotency_key,
            authorization=authorization,
        )

    @router.post(
        "/bff/management/data-sources/{source_instance_id}/actions/retire",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=SourceCommandReceiptEnvelope,
    )
    async def bff_retire_data_source(
        source_instance_id: str,
        body: RetireDataSourceRequest = Body(...),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """POST /bff/management/data-sources/{source_instance_id}/actions/retire"""
        return _execute_bff_source_command(
            command_type="retire",
            source_instance_id=source_instance_id,
            expected_revision=body.expected_revision,
            reason=body.reason,
            confirmation=body.confirmation,
            parameters={},
            trace_id=body.trace_id,
            idempotency_key=x_idempotency_key,
            authorization=authorization,
        )

    return router
