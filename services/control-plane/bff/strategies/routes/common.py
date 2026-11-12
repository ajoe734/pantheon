"""Common context and helpers for Strategies domain subrouters."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Mapping, Optional, Set, Tuple

from fastapi import HTTPException

from services.control_plane.bff.ports.read_surface_ports import ReadSurfacePorts
from ..service import (
    ROLE_ROLLBACK_BASELINE,
    ROLE_TRADING,
    StrategiesService,
    project_strategy_rules,
)


try:
    from services.control_plane.bff.ports.strategy_write_owner import StrategyWriteOwnerPort
except (ImportError, ValueError):
    from ..ports.strategy_write_owner import StrategyWriteOwnerPort  # type: ignore

try:
    from services.control_plane.bff.models import CommandType, ErrorCode, ObjectType, OperatorIdentity
except (ImportError, ValueError):
    from models import CommandType, ErrorCode, ObjectType, OperatorIdentity


def default_utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


_UNSET = object()

log = logging.getLogger(__name__)

# --- Strategy operational truth ------------------------------------------------
# The only place that derives ``operationalStatus`` and ``personaStates``. Persona
# state is never recomputed here: the caller hands in the Persona directory read
# and the Persona route's own projection, so the Strategy pages and
# ``GET /bff/personas`` agree by construction. Read-only.

STATUS_RUNNING = "running"
STATUS_IDLE = "idle"
STATUS_RETIRED = "retired"
STATUS_UNBOUND = "unbound"
STATUS_UNKNOWN = "unknown"

OPERATIONAL_STATUSES = (
    STATUS_RUNNING,
    STATUS_IDLE,
    STATUS_RETIRED,
    STATUS_UNBOUND,
    STATUS_UNKNOWN,
)

PERSONA_STATE_MISSING = "missing"
PERSONA_STATE_UNKNOWN = "unknown"


@dataclass(frozen=True)
class PersonaStateLookup:
    """One request's view of Persona states, read from the directory once."""

    available: bool
    states_by_id: Mapping[str, str] = field(default_factory=dict)
    operational_states: FrozenSet[str] = frozenset()
    failure: Optional[str] = None

    def persona_states(self, persona_ids: Iterable[str]) -> List[Dict[str, str]]:
        rows: List[Dict[str, str]] = []
        for persona_id in persona_ids:
            if not self.available:
                state = PERSONA_STATE_UNKNOWN
            else:
                state = self.states_by_id.get(persona_id, PERSONA_STATE_MISSING)
            rows.append({"personaId": persona_id, "state": state})
        return rows

    def operational_status(self, persona_ids: Iterable[str]) -> str:
        ids = list(persona_ids)
        if not self.available:
            return STATUS_UNKNOWN
        if not ids:
            return STATUS_UNBOUND
        states = [self.states_by_id.get(pid, PERSONA_STATE_MISSING) for pid in ids]
        if any(state in self.operational_states for state in states):
            return STATUS_RUNNING
        if all(state == STATUS_RETIRED for state in states):
            return STATUS_RETIRED
        return STATUS_IDLE

    def surface(self) -> Dict[str, Any]:
        if self.available:
            return {"status": "ok", "source": "persona_directory"}
        return {
            "status": "unavailable",
            "source": "persona_directory",
            "reason": self.failure or "persona directory read failed",
        }


def unavailable_lookup(reason: str) -> PersonaStateLookup:
    return PersonaStateLookup(available=False, failure=reason)


def read_persona_state_lookup(
    *,
    directory_snapshot: Optional[Callable[..., Any]],
    projected_state: Optional[Callable[[Mapping[str, Any]], str]],
    operational_states: Optional[Iterable[str]],
    tenant_id: str,
    snapshot_at: str,
) -> PersonaStateLookup:
    """Read the Persona directory once and project each record's state.

    ``directory_snapshot`` and ``projected_state`` are the same callables the
    Persona list route uses. Any failure yields an unavailable lookup so the
    caller reports ``unknown`` instead of hiding rows.
    """
    if directory_snapshot is None or projected_state is None or operational_states is None:
        return unavailable_lookup("persona directory is not wired for strategy reads")
    try:
        directory = directory_snapshot(tenant_id, snapshot_at=snapshot_at)
        states = {
            str(persona_id): str(projected_state(record))
            for persona_id, record in directory.records_by_id.items()
        }
    except Exception as exc:  # noqa: BLE001 - any owner failure degrades, never hides rows
        log.warning("strategy persona state read failed: %s", exc)
        return unavailable_lookup(f"persona directory read failed: {type(exc).__name__}")
    return PersonaStateLookup(
        available=True,
        states_by_id=states,
        operational_states=frozenset(str(s) for s in operational_states),
    )


def summary_persona_ids(row: Mapping[str, Any]) -> List[str]:
    raw = row.get("persona_ids")
    if not isinstance(raw, list):
        raw = row.get("personaIds")
    if not isinstance(raw, list):
        return []
    return [str(p) for p in raw if str(p).strip()]


def operational_counts(statuses: Iterable[str]) -> Dict[str, int]:
    counts = {name: 0 for name in OPERATIONAL_STATUSES}
    for status in statuses:
        counts[status] = counts.get(status, 0) + 1
    return counts


def _build_ranking_strategy_index(
    ranking_snapshot: Optional[Dict[str, Any]],
) -> Optional[Dict[str, List[Dict[str, Any]]]]:
    if not isinstance(ranking_snapshot, dict):
        return None
    items = ranking_snapshot.get("items")
    if not isinstance(items, (list, tuple)):
        return None
    index: Dict[str, List[Dict[str, Any]]] = {}
    for row in items:
        if not isinstance(row, dict):
            continue
        raw_strategy_ids = row.get("strategy_ids")
        if not isinstance(raw_strategy_ids, (list, tuple)):
            continue
        seen_in_row: Set[str] = set()
        for sid in raw_strategy_ids:
            normalized = str(sid or "").strip()
            if normalized and normalized not in seen_in_row:
                seen_in_row.add(normalized)
                index.setdefault(normalized, []).append(row)
    return index


def _project_strategy_ranking_metrics(
    strategy_id: str,
    ranking_snapshot: Optional[Dict[str, Any]],
    *,
    ranking_read_failed: bool = False,
    ranking_index: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    base_metrics: Dict[str, Any] = {
        "pnl": None,
        "pnl30d": None,
        "sharpe": None,
        "drawdown": None,
        "totalTrades": None,
        "metricsAsOf": None,
        "metricsResolution": "unavailable" if ranking_read_failed else "missing",
        "metricsPersonaId": None,
    }
    if ranking_read_failed or not strategy_id or not isinstance(ranking_snapshot, dict):
        return base_metrics
    if ranking_index is not None:
        matched_rows = ranking_index.get(strategy_id) or []
    else:
        items = ranking_snapshot.get("items")
        if not isinstance(items, (list, tuple)):
            return base_metrics
        matched_rows = []
        for row in items:
            if not isinstance(row, dict):
                continue
            raw_strategy_ids = row.get("strategy_ids")
            if not isinstance(raw_strategy_ids, (list, tuple)):
                continue
            row_strategy_ids = [str(sid).strip() for sid in raw_strategy_ids if str(sid or "").strip()]
            if strategy_id in row_strategy_ids:
                matched_rows.append(row)
    if not matched_rows:
        return base_metrics
    if len(matched_rows) > 1:
        base_metrics["metricsResolution"] = "ambiguous"
        return base_metrics
    row = matched_rows[0]
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    resolution = metrics.get("telemetry_resolution")
    if resolution is None:
        resolution = row.get("telemetry_resolution")
    resolution_str = str(resolution).strip() if resolution is not None else ""
    persona_id = row.get("persona_id")
    base_metrics["metricsPersonaId"] = persona_id if persona_id is not None else None
    if not resolution_str or resolution_str == "missing":
        base_metrics["metricsResolution"] = resolution_str or "missing"
        return base_metrics
    base_metrics["pnl"] = metrics.get("pnl")
    base_metrics["drawdown"] = metrics.get("drawdown")
    base_metrics["totalTrades"] = metrics.get("total_trades")
    base_metrics["metricsAsOf"] = metrics.get("latest_telemetry_at")
    base_metrics["metricsResolution"] = resolution_str
    return base_metrics


def _clean_str_list(value: Any) -> List[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [s.strip() for s in value if isinstance(s, str) and s.strip()]


def _project_strategy_spec_detail(source: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Project a bounded, allowlisted read-only spec view from an owner-resolved StrategySpec version."""
    if not isinstance(source, dict) or not source:
        return None

    top_meta = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
    nested_spec = (
        top_meta.get("strategy_spec")
        if isinstance(top_meta.get("strategy_spec"), dict)
        else (source.get("strategy_spec") if isinstance(source.get("strategy_spec"), dict) else {})
    )
    inner_meta = nested_spec.get("metadata") if isinstance(nested_spec.get("metadata"), dict) else {}

    raw_ver_id = (
        source.get("spec_version_id")
        or source.get("current_spec_version_id")
        or source.get("strategy_spec_registry_id")
    )
    spec_version_id = str(raw_ver_id).strip() if isinstance(raw_ver_id, str) and raw_ver_id.strip() else None

    raw_ver = (
        source.get("spec_version")
        or source.get("current_spec_version")
        or source.get("semantic_version")
        or source.get("version")
        or nested_spec.get("spec_version")
    )
    spec_version = str(raw_ver).strip() if isinstance(raw_ver, str) and raw_ver.strip() else None

    raw_ms = source.get("market_scope") if isinstance(source.get("market_scope"), dict) else {}
    if not raw_ms and isinstance(nested_spec.get("market_scope"), dict):
        raw_ms = nested_spec["market_scope"]
    ms_out: Dict[str, Any] = {}
    for list_key in ("symbols", "asset_classes", "venues", "markets"):
        cleaned = _clean_str_list(raw_ms.get(list_key))
        if cleaned:
            ms_out[list_key] = cleaned
    raw_universe = raw_ms.get("universe")
    if isinstance(raw_universe, (list, tuple)):
        cleaned_u = _clean_str_list(raw_universe)
        if cleaned_u:
            ms_out["universe"] = cleaned_u
    elif isinstance(raw_universe, str) and raw_universe.strip():
        ms_out["universe"] = raw_universe.strip()
    for str_key in ("frequency", "alpha"):
        val = raw_ms.get(str_key)
        if isinstance(val, str) and val.strip():
            ms_out[str_key] = val.strip()

    raw_ep = source.get("execution_profile") if isinstance(source.get("execution_profile"), dict) else {}
    if not raw_ep and isinstance(nested_spec.get("execution_profile"), dict):
        raw_ep = nested_spec["execution_profile"]
    ep_out: Dict[str, Any] = {}
    for str_key in (
        "signal_schema_version",
        "quantity_type",
        "rebalance_cadence",
        "execution_mode_hint",
        "mode",
        "capital_pool_id",
    ):
        val = raw_ep.get(str_key)
        if isinstance(val, str) and val.strip():
            ep_out[str_key] = val.strip()

    raw_ev = source.get("evaluation_plan") if isinstance(source.get("evaluation_plan"), dict) else {}
    if not raw_ev and isinstance(nested_spec.get("evaluation_plan"), dict):
        raw_ev = nested_spec["evaluation_plan"]
    ev_out: Dict[str, Any] = {}
    ev_metrics = _clean_str_list(raw_ev.get("metrics"))
    if ev_metrics:
        ev_out["metrics"] = ev_metrics
    for str_key in ("candidate_gate", "paper_gate", "live_gate", "benchmark"):
        val = raw_ev.get(str_key)
        if isinstance(val, str) and val.strip():
            ev_out[str_key] = val.strip()

    meta_out: Dict[str, Any] = {}
    for meta_key in ("market", "execution_context"):
        val = inner_meta.get(meta_key) if inner_meta.get(meta_key) not in (None, "") else top_meta.get(meta_key)
        if isinstance(val, str) and val.strip():
            meta_out[meta_key] = val.strip()

    top_traits = top_meta.get("traits") if isinstance(top_meta.get("traits"), dict) else {}
    inner_traits = inner_meta.get("traits") if isinstance(inner_meta.get("traits"), dict) else {}
    combined_traits = {**top_traits, **{k: v for k, v in inner_traits.items() if v not in (None, "")}}
    traits_out: Dict[str, Any] = {}
    raw_instruments = combined_traits.get("instruments")
    if isinstance(raw_instruments, str) and raw_instruments.strip():
        traits_out["instruments"] = raw_instruments.strip()
    elif isinstance(raw_instruments, (list, tuple)):
        cleaned_inst = _clean_str_list(raw_instruments)
        if cleaned_inst:
            traits_out["instruments"] = cleaned_inst
    raw_horizon = combined_traits.get("time_horizon")
    if isinstance(raw_horizon, str) and raw_horizon.strip():
        traits_out["time_horizon"] = raw_horizon.strip()
    if traits_out:
        meta_out["traits"] = traits_out

    def _spec_text(key: str) -> Optional[str]:
        for candidate in (source.get(key), nested_spec.get(key)):
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return None

    return {
        "spec_version_id": spec_version_id,
        "spec_version": spec_version,
        "title": _spec_text("title"),
        "hypothesis": _spec_text("hypothesis"),
        "objective": _spec_text("objective"),
        "market_scope": ms_out or None,
        "execution_profile": ep_out or None,
        "evaluation_plan": ev_out or None,
        "metadata": meta_out or None,
    }


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
    start = 0
    if page_token not in (None, ""):
        try:
            start = max(0, int(page_token))
        except (TypeError, ValueError):
            start = 0
    end = start + page_size
    next_page_token = str(end) if end < len(items) else None
    return list(items[start:end]), next_page_token


def default_read_surface_meta(
    surface: str,
    surface_key: str,
    *,
    snapshot_at: str,
    total: Optional[int] = None,
    state_counts: Any = _UNSET,
) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "snapshot_at": snapshot_at,
        "surface": surface,
        "surface_key": surface_key,
        "dataset": surface,
    }
    if total is not None:
        meta["total"] = total
    if state_counts is not _UNSET:
        meta["state_counts"] = state_counts
    return meta


@dataclass
class StrategyRouteContext:
    service: Optional[Any] = None
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
    resolve_final_idempotency_key: Callable[..., str] = lambda ik: str(ik or "")
    stable_json_hash: Callable[[Dict[str, Any]], str] = lambda d: ""
    request_dry_run_requested: Callable[[], bool] = lambda: False
    dry_run_success_response: Callable[..., Any] = lambda *a, **kw: {}
    normalize_lifecycle_state: Callable[[Any], str] = lambda s: str(s or "draft")
    normalize_risk_level: Callable[[Any], str] = lambda r: str(r or "medium")
    strategy_persona_idempotency_check: Callable[..., Optional[Dict[str, Any]]] = lambda k, h: None
    strategy_persona_idempotency: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    strategy_seed_replication_idempotency: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    strategy_seed_review_idempotency: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    list_governance_audit_events: Optional[Callable[[], List[Dict[str, Any]]]] = None
    ooda_packet_list_payload: Optional[Callable[..., Dict[str, Any]]] = None
    require_ooda_packet_routes_enabled: Optional[Callable[[], None]] = None
    bff_me_tenant_payload: Optional[Callable[..., Dict[str, Any]]] = None
    list_persona_records: Optional[Callable[..., List[Dict[str, Any]]]] = None
    # The Persona list route's own directory read and state projection, so the
    # Strategy pages report exactly the state GET /bff/personas reports.
    persona_directory_snapshot: Optional[Callable[..., Any]] = None
    persona_record_projected_state: Optional[Callable[[Any], str]] = None
    persona_operational_states: Optional[Any] = None
    list_strategy_summaries: Optional[Callable[[], List[Dict[str, Any]]]] = None
    strategy_write_owner: Optional[Union[StrategyWriteOwnerPort, Callable[[], StrategyWriteOwnerPort]]] = None
    get_strategy_write_owner: Optional[Callable[[], StrategyWriteOwnerPort]] = None

    def get_read_store_port(self) -> ReadSurfacePorts:
        if self.read_surface is not None:
            return self.read_surface() if callable(self.read_surface) else self.read_surface
        if self.get_read_store is not None:
            return self.get_read_store()
        raise NotImplementedError("Neither read_surface nor get_read_store dependency was supplied")

    def get_strategy_write_owner_port(self) -> Optional[StrategyWriteOwnerPort]:
        if self.strategy_write_owner is not None:
            return self.strategy_write_owner() if callable(self.strategy_write_owner) else self.strategy_write_owner
        if self.get_strategy_write_owner is not None:
            return self.get_strategy_write_owner()
        try:
            rs = self.get_read_store_port()
            if hasattr(rs, "upsert_strategy") or hasattr(rs, "create_strategy_spec"):
                return rs
        except Exception:
            pass
        return None

    def list_strategy_summaries_records(self) -> List[Dict[str, Any]]:
        if self.list_strategy_summaries is not None:
            return self.list_strategy_summaries()
        raise NotImplementedError("list_strategy_summaries dependency was not supplied")

    def bff_tenant_id(self, identity: Any) -> str:
        if self.bff_me_tenant_payload is None:
            raise NotImplementedError("bff_me_tenant_payload dependency was not supplied")
        return str(self.bff_me_tenant_payload(identity, requested_tenant=None)["id"])

    def read_persona_state_lookup(self, identity: Any, snapshot_at: str) -> PersonaStateLookup:
        """Read the Persona directory once for this request (tenant of the caller)."""
        try:
            tenant_id = self.bff_tenant_id(identity)
        except Exception as exc:  # noqa: BLE001 - no tenant means no persona truth
            return unavailable_lookup(f"persona tenant unresolved: {type(exc).__name__}")
        return read_persona_state_lookup(
            directory_snapshot=self.persona_directory_snapshot,
            projected_state=self.persona_record_projected_state,
            operational_states=self.persona_operational_states,
            tenant_id=tenant_id,
            snapshot_at=snapshot_at,
        )

    def write_principal(self, identity: Any) -> Dict[str, Any]:
        """Bind writes to the authenticated identity, never request-body fields."""
        claims = getattr(identity, "claims", {}) or {}
        tenant = str(claims.get("tenant") or claims.get("tenant_id") or claims.get("tenantId") or "").strip()
        if not tenant:
            tenants = claims.get("tenant_ids") or claims.get("tenantIds")
            if isinstance(tenants, list) and len(tenants) == 1:
                tenant = str(tenants[0] or "").strip()
        if not tenant:
            raise self.bff_error(403, ErrorCode.FORBIDDEN, "Verified tenant required",
                                 "Strategy writes require an authenticated tenant claim")
        return {
            "actor_id": identity.operator_id,
            "roles": list(identity.roles),
            "tenant": tenant,
            "token_kind": identity.token_kind,
        }

    def __post_init__(self) -> None:
        if self.service is None:
            from ..service import StrategiesService

            self.service = StrategiesService(
                read_surface=self.read_surface,
                get_read_store=self.get_read_store,
                strategy_write_owner=self.strategy_write_owner,
                get_strategy_write_owner=self.get_strategy_write_owner,
                list_strategy_summaries=self.list_strategy_summaries,
                bff_error=self.bff_error,
                utc_now=self.utc_now,
                normalize_lifecycle_state=self.normalize_lifecycle_state,
                normalize_risk_level=self.normalize_risk_level,
                stable_json_hash=self.stable_json_hash,
                idempotency_store=self.strategy_persona_idempotency,
                idempotency_check=self.strategy_persona_idempotency_check,
                seed_replication_idempotency=self.strategy_seed_replication_idempotency,
                seed_review_idempotency=self.strategy_seed_review_idempotency,
            )

    def ensure_strategy_exists(self, strategy_id: str) -> None:
        if self.service is not None:
            self.service.ensure_strategy_exists(strategy_id)
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
        ranking_snapshot: Any = _UNSET,
        ranking_read_failed: bool = False,
        ranking_index: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        include_spec: bool = False,
        persona_lookup: Optional[PersonaStateLookup] = None,
    ) -> Dict[str, Any]:
        if overlay is not None:
            raise AttributeError("strategy overlay is retired; process-local state overlays are deleted")
        strategy_id = str(summary.get("strategy_id") or summary.get("id") or "")
        title = summary.get("title") or summary.get("name")
        if not title and isinstance(summary.get("versions"), list):
            for v in summary.get("versions") or []:
                if isinstance(v, dict) and (v.get("name") or v.get("title")):
                    title = v.get("name") or v.get("title")
                    break
        title = title or strategy_id
        detail_dict = detail if isinstance(detail, dict) else {}
        lifecycle_raw = (
            detail_dict.get("lifecycle_state")
            or detail_dict.get("state")
            or detail_dict.get("status")
            or summary.get("lifecycle_state")
            or summary.get("state")
            or summary.get("status")
        )
        governance = detail_dict.get("governance") if detail else summary.get("governance")
        if not isinstance(governance, dict):
            governance = summary.get("governance") if isinstance(summary.get("governance"), dict) else {}
        market_scope = (detail or {}).get("market_scope") if detail else summary.get("market_scope")
        if not isinstance(market_scope, dict):
            market_scope = summary.get("market_scope") if isinstance(summary.get("market_scope"), dict) else {}
        execution_profile = (detail or {}).get("execution_profile") if detail else summary.get("execution_profile")
        if not isinstance(execution_profile, dict):
            execution_profile = summary.get("execution_profile") if isinstance(summary.get("execution_profile"), dict) else {}
        persona_ids: List[str] = []
        raw_persona_ids = (
            detail.get("persona_ids")
            if (detail and isinstance(detail.get("persona_ids"), list))
            else (summary.get("persona_ids") or summary.get("personaIds"))
        )
        if isinstance(raw_persona_ids, list):
            persona_ids = [str(p) for p in raw_persona_ids if str(p).strip()]
        detail_metadata = (
            detail.get("metadata")
            if detail and isinstance(detail.get("metadata"), dict)
            else {}
        )
        summary_metadata = (
            summary.get("metadata")
            if isinstance(summary.get("metadata"), dict)
            else {}
        )
        capital_pool_id = str(
            execution_profile.get("capital_pool_id")
            or governance.get("capital_pool_id")
            or summary.get("capital_pool_id")
            or summary.get("capitalPoolId")
            or detail_metadata.get("capital_pool_id")
            or detail_metadata.get("capitalPoolId")
            or summary_metadata.get("capital_pool_id")
            or summary_metadata.get("capitalPoolId")
            or (detail or {}).get("capital_pool_id")
            or (detail or {}).get("capitalPoolId")
            or ""
        ).strip()
        alpha = str(
            market_scope.get("alpha")
            or detail_dict.get("alpha")
            or detail_metadata.get("alpha")
            or summary.get("alpha")
            or summary_metadata.get("alpha")
            or ""
        )
        allowed = (detail or {}).get("allowedActions") or summary.get("allowedActions") or {}
        available_actions: List[str] = []
        if isinstance(allowed, dict):
            available_actions = sorted([k for k, v in allowed.items() if v])
        risk_raw = governance.get("risk_level") or summary.get("risk") or (detail or {}).get("risk")
        if ranking_snapshot is _UNSET:
            if self.service is not None:
                ranking_snapshot, ranking_read_failed = self.service.get_latest_ranking_snapshot()
            else:
                ranking_snapshot, ranking_read_failed = None, False
        metrics_fields = _project_strategy_ranking_metrics(
            strategy_id,
            ranking_snapshot if isinstance(ranking_snapshot, dict) else None,
            ranking_read_failed=bool(ranking_read_failed),
            ranking_index=ranking_index,
        )
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
            **metrics_fields,
            "availableActions": available_actions,
            "labelKey": f"strategy.{strategy_id}" if strategy_id else None,
            "lifecycleStatus": str(lifecycle_raw or ""),
            "symbols": _clean_str_list(market_scope.get("symbols")),
        }
        if persona_lookup is not None:
            dto["personaStates"] = persona_lookup.persona_states(persona_ids)
            dto["operationalStatus"] = persona_lookup.operational_status(persona_ids)
        if include_spec:
            spec_source = detail if (isinstance(detail, dict) and detail) else summary
            dto["spec"] = _project_strategy_spec_detail(spec_source)
        return dto
