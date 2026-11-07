"""BFF-AF-001: GET /bff/alpha-factory — replication admission & queue board."""
from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, Header, Query

try:
    from ..auth import policy as auth_policy
    from ..command_adapters.base import (
        ActionUnavailableError,
        bound_tenant,
        http_request_json_with_headers,
    )
    from ..core.owner_reads import authorization as owner_authorization, selected_tenant
except (ImportError, ValueError):
    try:
        from services.control_plane.bff.auth import policy as auth_policy
        from services.control_plane.bff.command_adapters.base import (
            ActionUnavailableError,
            bound_tenant,
            http_request_json_with_headers,
        )
        from services.control_plane.bff.core.owner_reads import authorization as owner_authorization, selected_tenant
    except ImportError:
        from auth import policy as auth_policy  # type: ignore
        from command_adapters.base import (  # type: ignore
            ActionUnavailableError,
            bound_tenant,
            http_request_json_with_headers,
        )
        from core.owner_reads import authorization as owner_authorization, selected_tenant  # type: ignore

_SURFACE_KEY = "alpha_factory"
_STRATEGY_STATE_SURFACE_KEY = "alpha_factory_strategy_state"
_OWNER_SOURCE = "research_orchestrator"
_ORCHESTRATOR_URL_ENV = "PANTHEON_RESEARCH_ORCHESTRATOR_API_URL"
_ORCHESTRATOR_ENTRIES_PATH = "/api/research-orchestrator/alpha-replication/entries"
_DISCOVERED_REASON = "no_candidate_source"

_LANES: List[Dict[str, Any]] = [
    {"id": "discovered", "label": "Discovered", "reason": _DISCOVERED_REASON},
    {"id": "scaffolded", "label": "Scaffolded"},
    {"id": "replicated", "label": "Replicated"},
]


def _resolve_orchestrator_url() -> Optional[str]:
    raw = (os.getenv(_ORCHESTRATOR_URL_ENV) or "").strip().rstrip("/")
    return raw or None


def _default_http_get(
    url: str,
    *,
    timeout: float = 3.0,
    auth_header: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> Tuple[int, Any]:
    auth = (auth_header if auth_header is not None else owner_authorization.get()) or ""
    auth = auth.strip()
    caller_token: Optional[str] = None
    if auth.lower().startswith("bearer "):
        caller_token = auth.split(" ", 1)[1].strip() or None
    elif auth:
        caller_token = auth
    if not caller_token:
        return 401, None
    eff_tenant = tenant_id if tenant_id is not None else selected_tenant.get()
    try:
        trusted_tenant = bound_tenant({}, tenant_id=eff_tenant, auth_token=caller_token)
        res = http_request_json_with_headers(
            url,
            method="GET",
            auth_token=caller_token,
            tenant_id=trusted_tenant,
            timeout=int(max(timeout, 1.0)),
        )
        if isinstance(res, tuple) and len(res) == 3:
            status_code, _headers, body = res
        else:
            status_code, body = res  # type: ignore[misc]
        return int(status_code), body
    except ActionUnavailableError:
        raise
    except Exception:
        return 503, None


def _resolve_verified_caller_tenant(
    identity: Any,
    requested_tenant: Optional[str],
) -> Optional[str]:
    clean_req = str(requested_tenant or "").strip() or None
    claim_tenants = auth_policy.identity_claim_strings(
        identity, auth_policy.TENANT_ALLOWED_CLAIM_PATHS
    )
    token_kind = getattr(identity, "token_kind", "stub")
    if claim_tenants or token_kind in ("jwt", "structured", "cookie"):
        tenant_payload = auth_policy.bff_me_tenant_payload(
            identity,
            requested_tenant=clean_req,
        )
        resolved = str(tenant_payload.get("id") or "").strip()
        return resolved or None
    return clean_req or auth_policy.first_nonblank(
        os.getenv("PANTHEON_BFF_TENANT_ID"),
        os.getenv("PANTHEON_BFF_DEFAULT_TENANT_ID"),
        os.getenv("PANTHEON_TENANT_ID"),
        "tenant-dev",
    )


def _extract_entries(payload: Any) -> Optional[List[Dict[str, Any]]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("items", "entries", "data"):
            val = payload.get(key)
            if isinstance(val, list):
                return [item for item in val if isinstance(item, dict)]
    return None


def _project_alpha_factory_card(entry: Dict[str, Any]) -> Dict[str, Any]:
    admission_status = (
        entry.get("admission_status")
        if entry.get("admission_status") is not None
        else entry.get("status")
    )
    queue_status = entry.get("queue_status")
    lane = (
        "replicated"
        if str(queue_status or "").strip().lower() == "completed"
        else "scaffolded"
    )
    raw_exp_runs = entry.get("experiment_run_ids")
    exp_run_ids = list(raw_exp_runs) if isinstance(raw_exp_runs, list) else None
    raw_auth_runs = entry.get("authority_run_ids")
    auth_run_ids = list(raw_auth_runs) if isinstance(raw_auth_runs, list) else None
    card_id = (
        entry.get("admission_id")
        or entry.get("id")
        or entry.get("strategy_spec_id")
    )
    return {
        "id": card_id,
        "admission_id": entry.get("admission_id"),
        "tenant_id": entry.get("tenant_id"),
        "lane": lane,
        "strategy_id": entry.get("strategy_id"),
        "strategy_spec_id": entry.get("strategy_spec_id"),
        "spec_version": entry.get("spec_version"),
        "checksum": entry.get("checksum"),
        "approval_decision_id": entry.get("approval_decision_id"),
        "approver": entry.get("approver"),
        "approved_at": entry.get("approved_at"),
        "requested_by": entry.get("requested_by"),
        "review_ref": entry.get("review_ref"),
        "mode": entry.get("mode"),
        "created_at": entry.get("created_at"),
        "status": admission_status,
        "admission_status": admission_status,
        "queue_status": queue_status,
        "enqueued_at": entry.get("enqueued_at"),
        "enqueued_by": entry.get("enqueued_by"),
        "attempt_count": entry.get("attempt_count"),
        "max_attempts": entry.get("max_attempts"),
        "experiment_task_id": entry.get("experiment_task_id"),
        "experiment_run_ids": exp_run_ids,
        "authority_task_id": entry.get("authority_task_id"),
        "authority_run_ids": auth_run_ids,
        "last_revalidation_at": entry.get("last_revalidation_at"),
        "last_error": entry.get("last_error"),
        "replay_count": entry.get("replay_count"),
        "last_replayed_at": entry.get("last_replayed_at"),
        "revalidation_count": entry.get("revalidation_count"),
        # Filled from the registry strategy reader after projection.
        "strategy_state": None,
    }


def _attach_strategy_state(
    cards: List[Dict[str, Any]],
    read_strategy_state: Optional[Callable[[str], Tuple[Optional[str], str]]],
) -> Dict[str, Any]:
    """Set ``strategy_state`` on each card from the registry strategy reader.

    One read per distinct strategy_id. A failed read leaves ``strategy_state``
    null on the affected cards and marks the surface unavailable; no card is
    hidden or moved because of the state.
    """
    source = "bff_strategy_registry"
    if read_strategy_state is None:
        return {"status": "unavailable", "source": source, "reason": "reader_unconfigured"}
    states: Dict[str, Tuple[Optional[str], str]] = {}
    failed = False
    for card in cards:
        strategy_id = str(card.get("strategy_id") or "").strip()
        if not strategy_id:
            continue
        if strategy_id not in states:
            try:
                states[strategy_id] = read_strategy_state(strategy_id)
            except Exception:  # noqa: BLE001 - degrade, never drop cards
                states[strategy_id] = (None, "unavailable")
        state, status = states[strategy_id]
        if status != "ok":
            failed = True
            card["strategy_state"] = None
        else:
            card["strategy_state"] = state
    if failed:
        return {"status": "unavailable", "source": source, "reason": "strategy_state_read_failed"}
    return {"status": "ok", "source": source}


def _build_alpha_factory_payload(
    snapshot_at: str,
    *,
    all_cards: List[Dict[str, Any]],
    page: int,
    page_size: int,
    lane: Optional[str],
    surface: Dict[str, Any],
    strategy_state_surface: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    is_unavailable = surface.get("status") == "unavailable"
    effective_cards = [] if is_unavailable else list(all_cards)
    discovered_items: List[Dict[str, Any]] = []
    scaffolded_items = [c for c in effective_cards if c.get("lane") == "scaffolded"]
    replicated_items = [c for c in effective_cards if c.get("lane") == "replicated"]

    if lane is not None:
        filtered = [c for c in effective_cards if c.get("lane") == lane]
    else:
        filtered = effective_cards

    total = len(filtered)
    start = (page - 1) * page_size
    page_items = filtered[start : start + page_size]
    next_page_token = str(page + 1) if (start + page_size) < total else None

    lanes_payload: List[Dict[str, Any]] = [
        {
            "id": "discovered",
            "label": "Discovered",
            "reason": _DISCOVERED_REASON,
            "items": discovered_items,
        },
        {
            "id": "scaffolded",
            "label": "Scaffolded",
            "items": scaffolded_items,
        },
        {
            "id": "replicated",
            "label": "Replicated",
            "items": replicated_items,
        },
    ]

    data: Dict[str, Any] = {
        "id": "alpha-factory",
        "snapshotAt": snapshot_at,
        "snapshot_at": snapshot_at,
        "reason": _DISCOVERED_REASON,
        "discovered_reason": _DISCOVERED_REASON,
        "lanes": lanes_payload,
        "discovered": discovered_items,
        "scaffolded": scaffolded_items,
        "replicated": replicated_items,
        "items": page_items,
    }
    surfaces: Dict[str, Any] = {_SURFACE_KEY: surface}
    if strategy_state_surface is not None:
        surfaces[_STRATEGY_STATE_SURFACE_KEY] = strategy_state_surface
    return {
        "data": data,
        "items": page_items,
        "page_info": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "next_page_token": next_page_token,
        },
        "meta": {
            "snapshot_at": snapshot_at,
            "surfaces": surfaces,
            "filters": {"lane": lane},
            "discovered": {"reason": _DISCOVERED_REASON},
        },
    }


def create_alpha_factory_router(
    *,
    extract_identity: Callable,
    require_read_role: Callable,
    utc_now: Callable[[], str],
    http_get: Optional[Callable[..., Tuple[Any, Any]]] = None,
    orchestrator_url_resolver: Optional[Callable[[], Optional[str]]] = None,
    read_surface: Optional[Any] = None,
    get_read_store: Optional[Callable[[], Any]] = None,
    read_strategy_state: Optional[Callable[[str], Tuple[Optional[str], str]]] = None,
) -> APIRouter:
    del read_surface, get_read_store
    router = APIRouter()
    eff_http_get = http_get or _default_http_get
    eff_url_resolver = orchestrator_url_resolver or _resolve_orchestrator_url

    @router.get("/bff/alpha-factory")
    def get_alpha_factory(
        lane: Optional[str] = Query(
            default=None,
            description="Filter by lane id: discovered | scaffolded | replicated",
        ),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        """BFF-AF-001: AlphaFactory replication admission & queue kanban board."""
        eff_auth = authorization or owner_authorization.get()
        identity = extract_identity(eff_auth)
        require_read_role(identity)

        req_tenant = x_tenant_id if x_tenant_id is not None else selected_tenant.get()
        verified_tenant = _resolve_verified_caller_tenant(identity, req_tenant)

        snapshot_at = utc_now()
        base_url = eff_url_resolver()
        if not base_url or not verified_tenant:
            surface: Dict[str, Any] = {
                "status": "unavailable",
                "source": _OWNER_SOURCE,
                "reason": "unconfigured" if not base_url else "missing_tenant_scope",
                "staleness": {"served_from": "unverifiable", "last_known_at": snapshot_at},
            }
            return _build_alpha_factory_payload(
                snapshot_at,
                all_cards=[],
                page=page,
                page_size=page_size,
                lane=lane,
                surface=surface,
            )

        url = f"{base_url.rstrip('/')}{_ORCHESTRATOR_ENTRIES_PATH}"
        try:
            try:
                status, raw_payload = eff_http_get(
                    url,
                    auth_header=eff_auth,
                    tenant_id=verified_tenant,
                )
            except TypeError:
                status, raw_payload = eff_http_get(url)
        except ActionUnavailableError:
            raise
        except Exception:
            status, raw_payload = 503, None

        if isinstance(status, bool):
            ok = status is True
        else:
            ok = int(status) == 200

        entries = _extract_entries(raw_payload) if ok else None
        if not ok or entries is None:
            surface = {
                "status": "unavailable",
                "source": _OWNER_SOURCE,
                "reason": "upstream_error",
                "staleness": {"served_from": "unverifiable", "last_known_at": snapshot_at},
            }
            return _build_alpha_factory_payload(
                snapshot_at,
                all_cards=[],
                page=page,
                page_size=page_size,
                lane=lane,
                surface=surface,
            )

        cards: List[Dict[str, Any]] = []
        for entry in entries:
            entry_tenant = str(entry.get("tenant_id") or "").strip()
            if entry_tenant and entry_tenant != verified_tenant:
                continue
            cards.append(_project_alpha_factory_card(entry))

        strategy_state_surface = _attach_strategy_state(cards, read_strategy_state)

        surface = {
            "status": "ok",
            "source": _OWNER_SOURCE,
        }
        return _build_alpha_factory_payload(
            snapshot_at,
            all_cards=cards,
            page=page,
            page_size=page_size,
            lane=lane,
            surface=surface,
            strategy_state_surface=strategy_state_surface,
        )

    return router
