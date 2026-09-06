"""Common context and helpers for Strategies domain subrouters."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from fastapi import HTTPException

try:
    from services.control_plane.bff.models import CommandType, ErrorCode, ObjectType, OperatorIdentity
except (ImportError, ValueError):
    from models import CommandType, ErrorCode, ObjectType, OperatorIdentity


def default_utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_bff_error(
    status_code: int,
    code: str,
    message: str,
    reason: Optional[str] = None,
    precondition_failed: Optional[str] = None,
    suggestion: Optional[str] = None,
    details_extra: Optional[Dict[str, Any]] = None,
) -> HTTPException:
    detail: Dict[str, Any] = {
        "error": {
            "code": code,
            "message": message,
            "reason": reason or message,
            "status_code": status_code,
        }
    }
    if precondition_failed:
        detail["error"]["details"] = {"precondition_failed": precondition_failed}
    if suggestion:
        detail["error"]["suggestion"] = suggestion
    if details_extra:
        detail["error"].setdefault("details", {}).update(details_extra)
    return HTTPException(status_code=status_code, detail=detail)


def default_extract_identity(authorization: Optional[str] = None) -> Any:
    class DummyIdentity:
        operator_id = "op-user"
        roles = {"operator", "admin", "viewer"}

    ident = DummyIdentity()
    if authorization:
        token = authorization.replace("Bearer ", "").strip()
        parts = token.split(":")
        ident.operator_id = parts[0]
        if len(parts) > 1:
            ident.roles = set(parts[1].split(","))
    return ident


def default_require_read_role(identity: Any) -> None:
    pass


def default_require_operator_role(identity: Any, err_fn=None) -> None:
    roles = getattr(identity, "roles", set())
    if not ({"operator", "admin", "approver"}.intersection(roles)):
        _err = err_fn or default_bff_error
        raise _err(
            403,
            ErrorCode.FORBIDDEN,
            "Operator role required",
            "Operator role required to mutate strategies",
            precondition_failed="role_check",
        )


def default_page_slice(
    items: List[Dict[str, Any]], page_token: Optional[str], page_size: int
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    return list(items[:page_size]), None


def default_read_surface_meta(
    surface: str,
    surface_key: str,
    *,
    snapshot_at: str,
    total: Optional[int] = None,
) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "snapshot_at": snapshot_at,
        "surface": surface,
        "surface_key": surface_key,
        "dataset": surface,
    }
    if total is not None:
        meta["total"] = total
    return meta


@dataclass
class StrategyRouteContext:
    read_surface: Optional[Any] = None
    get_read_store: Optional[Callable[[], Any]] = None
    extract_identity: Callable[..., Any] = default_extract_identity
    require_read_role: Callable[..., None] = default_require_read_role
    require_operator_role: Callable[..., None] = default_require_operator_role
    bff_error: Callable[..., HTTPException] = default_bff_error
    utc_now: Callable[[], str] = default_utc_now
    page_slice: Callable[..., Any] = default_page_slice
    read_surface_meta: Callable[..., Dict[str, Any]] = default_read_surface_meta
    reject_body_idempotency_key: Callable[[Dict[str, Any]], None] = lambda p: None
    resolve_final_idempotency_key: Callable[..., str] = lambda ik, xik: str(ik or xik or "")
    stable_json_hash: Callable[[Dict[str, Any]], str] = lambda d: ""
    request_dry_run_requested: Callable[[], bool] = lambda: False
    dry_run_success_response: Callable[..., Any] = lambda *a, **kw: {}
    normalize_lifecycle_state: Callable[[Any], str] = lambda s: str(s or "draft")
    normalize_risk_level: Callable[[Any], str] = lambda r: str(r or "medium")
    strategy_persona_idempotency_check: Callable[..., Optional[Dict[str, Any]]] = lambda k, h: None
    strategy_persona_action_command: Optional[Callable[..., Any]] = None
    strategy_persona_idempotency: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    strategy_seed_replication_idempotency: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    strategy_seed_review_idempotency: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    list_governance_audit_events: Optional[Callable[[], List[Dict[str, Any]]]] = None
    ooda_packet_list_payload: Optional[Callable[..., Dict[str, Any]]] = None
    require_ooda_packet_routes_enabled: Optional[Callable[[], None]] = None
    deprecated_bff_path_response: Optional[Callable[..., Any]] = None
    bff_me_tenant_payload: Optional[Callable[..., Dict[str, Any]]] = None
    list_persona_records: Optional[Callable[..., List[Dict[str, Any]]]] = None
    list_strategy_summaries: Optional[Callable[[], List[Dict[str, Any]]]] = None

    def get_read_store_port(self) -> Any:
        if self.read_surface is not None:
            return self.read_surface() if callable(self.read_surface) else self.read_surface
        if self.get_read_store is not None:
            return self.get_read_store()
        raise NotImplementedError("Neither read_surface nor get_read_store dependency was supplied")

    def list_strategy_summaries_records(self) -> List[Dict[str, Any]]:
        if self.list_strategy_summaries is not None:
            return self.list_strategy_summaries()
        raise NotImplementedError("list_strategy_summaries dependency was not supplied")

    def bff_tenant_id(self, identity: Any) -> str:
        if self.bff_me_tenant_payload is None:
            raise NotImplementedError("bff_me_tenant_payload dependency was not supplied")
        return str(self.bff_me_tenant_payload(identity, requested_tenant=None)["id"])

    def ensure_strategy_exists(self, strategy_id: str) -> None:
        read_store = self.get_read_store_port()
        found = False
        getter = getattr(read_store, "get_strategy_spec", None)
        if callable(getter):
            try:
                found = bool(getter(strategy_id))
            except Exception:
                pass
        if not found:
            getter = getattr(read_store, "get_strategy", None)
            if callable(getter):
                try:
                    found = bool(getter(strategy_id))
                except Exception:
                    pass
        if found:
            return
        raise self.bff_error(
            404,
            ErrorCode.RESOURCE_NOT_FOUND,
            "Strategy not found",
            f"Strategy {strategy_id} does not exist",
        )

    def project_strategy_dto(
        self,
        summary: Dict[str, Any],
        *,
        detail: Optional[Dict[str, Any]] = None,
        overlay: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        strategy_id = str(summary.get("strategy_id") or summary.get("id") or "")
        title = summary.get("title") or summary.get("name")
        if not title and isinstance(summary.get("versions"), list):
            for v in summary.get("versions") or []:
                if isinstance(v, dict) and (v.get("name") or v.get("title")):
                    title = v.get("name") or v.get("title")
                    break
        title = title or strategy_id
        lifecycle_raw = (detail or summary).get("lifecycle_state") or summary.get("lifecycle_state")
        governance = (detail or {}).get("governance") if detail else {}
        governance = governance if isinstance(governance, dict) else (summary.get("governance") or {})
        governance = governance if isinstance(governance, dict) else {}
        market_scope = (detail or {}).get("market_scope") if detail else {}
        market_scope = market_scope if isinstance(market_scope, dict) else {}
        execution_profile = (detail or {}).get("execution_profile") if detail else {}
        execution_profile = execution_profile if isinstance(execution_profile, dict) else {}
        persona_ids: List[str] = []
        if detail and isinstance(detail.get("persona_ids"), list):
            persona_ids = [str(p) for p in detail.get("persona_ids") or [] if str(p).strip()]
        capital_pool_id = str(
            execution_profile.get("capital_pool_id")
            or governance.get("capital_pool_id")
            or summary.get("capital_pool_id")
            or ""
        )
        alpha = str(
            market_scope.get("alpha")
            or summary.get("source_kind")
            or summary.get("hypothesis_excerpt")
            or ""
        )
        allowed = (detail or {}).get("allowedActions") or {}
        available_actions: List[str] = []
        if isinstance(allowed, dict):
            available_actions = sorted([k for k, v in allowed.items() if v])
        risk_raw = governance.get("risk_level") or summary.get("risk") or (detail or {}).get("risk")
        dto: Dict[str, Any] = {
            "id": strategy_id,
            "name": title,
            "owner": summary.get("owner") or governance.get("owner") or "pantheon-bff",
            "updatedAt": summary.get("last_modified_at")
            or summary.get("updated_at")
            or (detail or {}).get("created_at")
            or self.utc_now(),
            "state": self.normalize_lifecycle_state(lifecycle_raw),
            "risk": self.normalize_risk_level(risk_raw),
            "alpha": alpha,
            "capitalPoolId": capital_pool_id,
            "personaIds": persona_ids,
            "pnl30d": 0.0,
            "sharpe": 0.0,
            "drawdown": 0.0,
            "availableActions": available_actions,
            "labelKey": f"strategy.{strategy_id}" if strategy_id else None,
            "lifecycleStatus": str(lifecycle_raw or ""),
        }
        if overlay:
            for k, v in overlay.items():
                if v is not None:
                    dto[k] = v
        return dto
