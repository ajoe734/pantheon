"""Agora dashboard v2 router — agora.dashboard.v2.

Authority:
  - docs/04/.../contract-closure/04_dashboard_crud_and_concurrency.md  (§17.5 routes)
  - docs/04/.../design-closure/A3_widget_registry_and_chart_grammar_spec.md  (§7 validator)
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, Header, HTTPException, Query, Response

# ── Widget registry ──────────────────────────────────────────────────────────

_WIDGET_REGISTRY_PATH = os.path.normpath(os.path.join(
    os.path.dirname(__file__),
    "..", "..", "..",
    "specs", "agora", "widget_registry.v1.json",
))

_REGISTRY_VERSION = "widget_registry.v1"

_SENSITIVITY_RANK: Dict[str, int] = {
    "public_market": 0,
    "user_private": 1,
    "broker_sensitive": 2,
    "restricted": 3,
}

_FORBIDDEN_INTERACTIONS = frozenset({
    "place_order",
    "enable_live",
    "change_capital_binding",
    "invoke_broker",
    "write_runtime_binding",
    "open_management_route",
})

_VALID_LAYOUT_OPS = frozenset({
    "move_widget",
    "resize_widget",
    "remove_widget",
    "add_registered_widget",
    "replace_chart_spec",
    "update_widget_query",
})


def _load_widget_registry() -> Tuple[Dict[str, Any], str]:
    try:
        with open(_WIDGET_REGISTRY_PATH) as f:
            raw = f.read()
        data = json.loads(raw)
        registry = {entry["widget_type"]: entry for entry in data.get("entries", [])}
        schema_hash = hashlib.sha256(raw.encode()).hexdigest()
        return registry, schema_hash
    except (OSError, json.JSONDecodeError):
        return {}, ""


_WIDGET_REGISTRY, _REGISTRY_SCHEMA_HASH = _load_widget_registry()

# ── In-memory stores ─────────────────────────────────────────────────────────
# dashboard_recipe_identity:  recipe_id → {recipe_id, tenant_id, user_id, strategy_id, active_version, created_at}
# dashboard_recipe_versions:  (recipe_id, version) → {recipe_id, version, previous_version, status,
#                                                      recipe_json, content_sha256, generated_by,
#                                                      change_reason, created_at}

_recipe_identity: Dict[str, dict] = {}
_recipe_versions: Dict[Tuple[str, int], dict] = {}
_recipe_feedback: List[dict] = []
_widget_feedback: List[dict] = []
_plugin_proposals: List[dict] = []
_seen_idempotency_keys: set = set()

# ── Helpers ───────────────────────────────────────────────────────────────────


def _sha256(data: Any) -> str:
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _make_etag(recipe_id: str, version: int, content_sha256: str) -> str:
    return f'"recipe:{recipe_id}:v{version}:{content_sha256[:8]}"'


def _get_version_record(recipe_id: str, version: int) -> Optional[dict]:
    return _recipe_versions.get((recipe_id, version))


def _validate_widget_spec(widget: dict) -> dict:
    """Validate a WidgetSpec v2 payload per A3 §7 rules."""
    errors: List[dict] = []
    warnings: List[dict] = []

    widget_type = widget.get("widget_type", "")
    entry = _WIDGET_REGISTRY.get(widget_type)

    # Rule 1: widgetType exists and status active
    if not entry:
        errors.append({
            "code": "WIDGET_TYPE_NOT_FOUND",
            "path": "widget_type",
            "message": f"Widget type '{widget_type}' not found in widget_registry.v1",
        })
        return {"valid": False, "errors": errors, "warnings": warnings,
                "registry_version": _REGISTRY_VERSION, "schema_hash": _REGISTRY_SCHEMA_HASH}

    if entry.get("status") != "active":
        errors.append({
            "code": "WIDGET_TYPE_NOT_ACTIVE",
            "path": "widget_type",
            "message": f"Widget type '{widget_type}' status is '{entry.get('status')}'; must be 'active'",
        })

    chart_spec = widget.get("chart_spec") or {}
    chart_kind = chart_spec.get("kind")
    allowed_chart_kinds: List[str] = entry.get("allowed_chart_kinds", [])

    # Rule 2: chartSpec.kind in entry allowlist
    if chart_kind and allowed_chart_kinds and chart_kind not in allowed_chart_kinds:
        errors.append({
            "code": "CHART_KIND_NOT_ALLOWED",
            "path": "chart_spec.kind",
            "message": f"Chart kind '{chart_kind}' not allowed; allowed: {allowed_chart_kinds}",
        })

    # Rule 3: dataSource in entry allowlist
    data_source_id = widget.get("data_source_id", "")
    allowed_data_sources: List[str] = entry.get("allowed_data_sources", [])
    if data_source_id not in allowed_data_sources:
        errors.append({
            "code": "DATA_SOURCE_NOT_ALLOWED",
            "path": "data_source_id",
            "message": f"Data source '{data_source_id}' not allowed; allowed: {allowed_data_sources}",
        })

    # Rule 5: transforms all in allowlist
    allowed_transforms = set(entry.get("allowed_transforms", []))
    for t in (chart_spec.get("transforms") or []):
        t_type = t.get("type", "")
        if t_type and allowed_transforms and t_type not in allowed_transforms:
            errors.append({
                "code": "TRANSFORM_NOT_ALLOWED",
                "path": "chart_spec.transforms",
                "message": f"Transform '{t_type}' not in allowed transforms",
            })

    # Rule 6: interactions all in allowlist and not forbidden
    allowed_interactions = set(entry.get("allowed_interactions", []))
    for interaction in (widget.get("interactions") or []):
        kind = interaction.get("kind", "")
        if kind in _FORBIDDEN_INTERACTIONS:
            errors.append({
                "code": "INTERACTION_FORBIDDEN",
                "path": "interactions",
                "message": f"Interaction kind '{kind}' is forbidden",
            })
        elif allowed_interactions and kind not in allowed_interactions:
            errors.append({
                "code": "INTERACTION_NOT_ALLOWED",
                "path": "interactions",
                "message": f"Interaction kind '{kind}' not in allowed interactions",
            })

    click_action = chart_spec.get("click_action") or {}
    if click_action:
        kind = click_action.get("kind", "")
        if kind in _FORBIDDEN_INTERACTIONS:
            errors.append({
                "code": "INTERACTION_FORBIDDEN",
                "path": "chart_spec.click_action.kind",
                "message": f"Click action kind '{kind}' is forbidden",
            })

    # Rule 8: sensitivity not downgraded vs registry minimum
    widget_sensitivity = widget.get("sensitivity", "")
    entry_sensitivity = entry.get("sensitivity", "")
    if _SENSITIVITY_RANK.get(widget_sensitivity, -1) < _SENSITIVITY_RANK.get(entry_sensitivity, 0):
        errors.append({
            "code": "SENSITIVITY_DOWNGRADED",
            "path": "sensitivity",
            "message": (
                f"Widget sensitivity '{widget_sensitivity}' is lower than "
                f"registry minimum '{entry_sensitivity}'"
            ),
        })

    # Rule 9 / Rule 11: query limit and node limits
    query = widget.get("query") or {}
    limit = query.get("limit")
    if limit is not None:
        if limit > 10000:
            errors.append({
                "code": "QUERY_LIMIT_EXCEEDED",
                "path": "query.limit",
                "message": f"query.limit {limit} exceeds maximum 10000",
            })
        if chart_kind in ("network", "sankey") and limit > 500:
            errors.append({
                "code": "NODE_LIMIT_EXCEEDED",
                "path": "query.limit",
                "message": f"network/sankey query.limit {limit} exceeds 500-node maximum",
            })

    # Rule 10: forbidden content in chart_spec.options (JS/HTML injection)
    options_str = json.dumps(chart_spec.get("options") or {})
    for forbidden_pat in ("<script", "javascript:", "data:text/html"):
        if forbidden_pat in options_str.lower():
            errors.append({
                "code": "FORBIDDEN_CONTENT",
                "path": "chart_spec.options",
                "message": f"Forbidden content pattern '{forbidden_pat}' detected in chart_spec.options",
            })
            break

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "registry_version": _REGISTRY_VERSION,
        "schema_hash": _REGISTRY_SCHEMA_HASH,
    }


def _apply_layout_ops(recipe: dict, operations: List[dict]) -> dict:
    """Apply layout-patch operations to a recipe, returning a new copy."""
    recipe = copy.deepcopy(recipe)
    views: List[dict] = recipe.get("views") or []

    for op in operations:
        op_name = op.get("op")
        widget_id = op.get("widget_id")
        payload: dict = op.get("payload") or {}

        if op_name == "move_widget":
            for view in views:
                for placement in (view.get("placements") or []):
                    if placement.get("widget_id") == widget_id:
                        if "x" in payload:
                            placement["x"] = payload["x"]
                        if "y" in payload:
                            placement["y"] = payload["y"]

        elif op_name == "resize_widget":
            for view in views:
                for placement in (view.get("placements") or []):
                    if placement.get("widget_id") == widget_id:
                        if "w" in payload:
                            placement["w"] = payload["w"]
                        if "h" in payload:
                            placement["h"] = payload["h"]

        elif op_name == "remove_widget":
            for view in views:
                view["placements"] = [
                    p for p in (view.get("placements") or [])
                    if p.get("widget_id") != widget_id
                ]
                view["widgets"] = [
                    w for w in (view.get("widgets") or [])
                    if w.get("widget_id") != widget_id
                ]

        elif op_name == "add_registered_widget":
            view_id = payload.get("view_id")
            widget_spec = payload.get("widget_spec") or {}
            placement = payload.get("placement") or {}
            for view in views:
                if view_id is None or view.get("view_id") == view_id:
                    view.setdefault("widgets", []).append(widget_spec)
                    view.setdefault("placements", []).append(placement)
                    break

        elif op_name == "replace_chart_spec":
            new_chart_spec = payload.get("chart_spec") or {}
            for view in views:
                for widget in (view.get("widgets") or []):
                    if widget.get("widget_id") == widget_id:
                        widget["chart_spec"] = new_chart_spec

        elif op_name == "update_widget_query":
            new_query = payload.get("query") or {}
            for view in views:
                for widget in (view.get("widgets") or []):
                    if widget.get("widget_id") == widget_id:
                        widget["query"] = new_query

    recipe["views"] = views
    return recipe


# ── Router factory ────────────────────────────────────────────────────────────


def create_dashboard_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
) -> APIRouter:
    """Dashboard v2 router — agora.dashboard.v2.

    Implements the 11 canonical routes from contract-closure §04 with
    append-only versioning and ETag-based optimistic concurrency.
    """
    router = APIRouter(tags=["agora-dashboard-v2"])

    # ── GET /bff/agora/strategies/{strategy_id}/dashboard-recipes ─────────
    @router.get("/bff/agora/strategies/{strategy_id}/dashboard-recipes")
    def list_dashboard_recipes(
        strategy_id: str,
        authorization: Optional[str] = Header(default=None),
        workspace: Optional[str] = Query(default=None),
        phase: Optional[str] = Query(default=None),
        cursor: Optional[str] = Query(default=None),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)

        items = []
        for recipe_id, ident in _recipe_identity.items():
            if ident.get("strategy_id") != strategy_id:
                continue
            ver = _get_version_record(recipe_id, ident["active_version"])
            if ver is None:
                continue
            rj = ver.get("recipe_json") or {}
            if workspace and rj.get("workspace") != workspace:
                continue
            if phase and rj.get("phase") != phase:
                continue
            items.append({
                "recipe_id": recipe_id,
                "version": ident["active_version"],
                "workspace": rj.get("workspace"),
                "phase": rj.get("phase"),
                "status": ver.get("status"),
                "created_at": ident.get("created_at"),
                "updated_at": rj.get("updated_at"),
            })

        items.sort(key=lambda r: r["recipe_id"])
        if cursor:
            items = [i for i in items if i["recipe_id"] > cursor]
        page = items[:limit]
        next_cursor = page[-1]["recipe_id"] if len(page) == limit else None

        return {
            "data": page,
            "meta": {
                "snapshot_at": utc_now(),
                "capability": "agora.dashboard.v2",
                "strategy_id": strategy_id,
                "next_cursor": next_cursor,
            },
        }

    # ── POST /bff/agora/strategies/{strategy_id}/dashboard-recipes/proposals
    @router.post(
        "/bff/agora/strategies/{strategy_id}/dashboard-recipes/proposals",
        status_code=201,
    )
    def propose_dashboard_recipe(
        strategy_id: str,
        body: Dict[str, Any],
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, Any]:
        from models import ErrorCode  # noqa: PLC0415
        identity = extract_identity(authorization)
        require_read_role(identity)

        strategy_version_id = body.get("strategy_version_id", "")
        workspace = body.get("workspace", "")
        phase = body.get("phase", "")

        if not strategy_version_id:
            raise bff_error(400, ErrorCode.VALIDATION_FAILED, "strategy_version_id required", "missing_field")
        if workspace not in {"trading_room", "strategy_workshop", "strategy_performance"}:
            raise bff_error(400, ErrorCode.VALIDATION_FAILED, f"Invalid workspace '{workspace}'", "invalid_workspace")
        if phase not in {"candidate_review", "monitoring", "position_monitoring", "post_trade_review"}:
            raise bff_error(400, ErrorCode.VALIDATION_FAILED, f"Invalid phase '{phase}'", "invalid_phase")

        if idempotency_key and idempotency_key in _seen_idempotency_keys:
            for recipe_id, ident in _recipe_identity.items():
                if ident.get("strategy_id") != strategy_id:
                    continue
                ver = _get_version_record(recipe_id, ident["active_version"])
                if ver and ver.get("status") == "proposal":
                    rj = ver.get("recipe_json") or {}
                    if rj.get("workspace") == workspace and rj.get("phase") == phase:
                        return {
                            "data": {
                                "recipe_id": recipe_id,
                                "version": ident["active_version"],
                                "status": "proposal",
                            },
                            "meta": {"capability": "agora.dashboard.v2", "snapshot_at": utc_now()},
                        }

        now = utc_now()
        recipe_id = f"rec_{uuid.uuid4().hex[:12]}"
        tenant_id = getattr(identity, "tenant_id", "") or ""
        user_id = getattr(identity, "user_id", "") or ""

        recipe_json: Dict[str, Any] = {
            "spec_version": "2.0",
            "recipe_id": recipe_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "strategy_id": strategy_id,
            "strategy_version_id": strategy_version_id,
            "workspace": workspace,
            "phase": phase,
            "views": [],
            "generated_by": "servant",
            "change_reason": body.get("instruction") or "Initial proposal",
            "version": 1,
            "previous_version": None,
            "status": "proposal",
            "created_at": now,
            "updated_at": now,
        }
        content_sha256 = _sha256(recipe_json)
        recipe_json["content_sha256"] = content_sha256

        _recipe_identity[recipe_id] = {
            "recipe_id": recipe_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "strategy_id": strategy_id,
            "active_version": 1,
            "created_at": now,
        }
        _recipe_versions[(recipe_id, 1)] = {
            "recipe_id": recipe_id,
            "version": 1,
            "previous_version": None,
            "status": "proposal",
            "recipe_json": recipe_json,
            "content_sha256": content_sha256,
            "generated_by": "servant",
            "change_reason": body.get("instruction") or "Initial proposal",
            "created_at": now,
        }

        if idempotency_key:
            _seen_idempotency_keys.add(idempotency_key)

        return {
            "data": {
                "recipe_id": recipe_id,
                "version": 1,
                "status": "proposal",
                "strategy_id": strategy_id,
                "workspace": workspace,
                "phase": phase,
                "created_at": now,
            },
            "meta": {"capability": "agora.dashboard.v2", "snapshot_at": utc_now()},
        }

    # ── GET /bff/agora/dashboard-recipes/{recipe_id} ──────────────────────
    @router.get("/bff/agora/dashboard-recipes/{recipe_id}")
    def get_dashboard_recipe(
        recipe_id: str,
        response: Response,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        from models import ErrorCode  # noqa: PLC0415
        identity = extract_identity(authorization)
        require_read_role(identity)

        ident = _recipe_identity.get(recipe_id)
        if not ident:
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, f"Recipe '{recipe_id}' not found", "recipe_not_found")

        ver = _get_version_record(recipe_id, ident["active_version"])
        if not ver:
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Version record missing", "version_not_found")

        etag = _make_etag(recipe_id, ident["active_version"], ver["content_sha256"])
        response.headers["ETag"] = etag

        return {
            "data": ver["recipe_json"],
            "meta": {
                "snapshot_at": utc_now(),
                "capability": "agora.dashboard.v2",
                "etag": etag,
                "version": ident["active_version"],
                "status": ver["status"],
            },
        }

    # ── POST /bff/agora/dashboard-recipes/{recipe_id}/accept ──────────────
    @router.post("/bff/agora/dashboard-recipes/{recipe_id}/accept")
    def accept_dashboard_recipe(
        recipe_id: str,
        body: Dict[str, Any],
        response: Response,
        authorization: Optional[str] = Header(default=None),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, Any]:
        from models import ErrorCode  # noqa: PLC0415
        identity = extract_identity(authorization)
        require_read_role(identity)

        if not if_match:
            raise bff_error(400, ErrorCode.VALIDATION_FAILED, "If-Match header required", "missing_if_match")
        expected_version = body.get("expected_version")
        if not isinstance(expected_version, int):
            raise bff_error(400, ErrorCode.VALIDATION_FAILED, "expected_version (integer) required", "missing_field")

        ident = _recipe_identity.get(recipe_id)
        if not ident:
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, f"Recipe '{recipe_id}' not found", "recipe_not_found")

        active_version = ident["active_version"]
        cur_ver = _get_version_record(recipe_id, active_version)
        if not cur_ver:
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Version record missing", "version_not_found")

        current_etag = _make_etag(recipe_id, active_version, cur_ver["content_sha256"])
        if if_match != current_etag or expected_version != active_version:
            raise bff_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                "Dashboard recipe changed after the client snapshot.",
                "etag_mismatch",
                details_extra={
                    "expected_version": expected_version,
                    "current_version": active_version,
                    "current_etag": current_etag,
                    "latest_href": f"/bff/agora/dashboard-recipes/{recipe_id}",
                },
            )

        if idempotency_key and idempotency_key in _seen_idempotency_keys:
            response.headers["ETag"] = current_etag
            return {
                "data": {"recipe_id": recipe_id, "version": active_version, "status": cur_ver["status"]},
                "meta": {"capability": "agora.dashboard.v2", "snapshot_at": utc_now(), "etag": current_etag},
            }

        now = utc_now()
        new_version = active_version + 1
        new_recipe = copy.deepcopy(cur_ver["recipe_json"])
        new_recipe["version"] = new_version
        new_recipe["previous_version"] = active_version
        new_recipe["status"] = "active"
        new_recipe["change_reason"] = body.get("note") or "Accepted proposal"
        new_recipe["updated_at"] = now
        new_recipe.pop("content_sha256", None)
        new_sha256 = _sha256(new_recipe)
        new_recipe["content_sha256"] = new_sha256

        _recipe_versions[(recipe_id, new_version)] = {
            "recipe_id": recipe_id,
            "version": new_version,
            "previous_version": active_version,
            "status": "active",
            "recipe_json": new_recipe,
            "content_sha256": new_sha256,
            "generated_by": cur_ver.get("generated_by", "user"),
            "change_reason": body.get("note") or "Accepted proposal",
            "created_at": now,
        }
        _recipe_identity[recipe_id]["active_version"] = new_version

        if idempotency_key:
            _seen_idempotency_keys.add(idempotency_key)

        new_etag = _make_etag(recipe_id, new_version, new_sha256)
        response.headers["ETag"] = new_etag

        return {
            "data": {"recipe_id": recipe_id, "version": new_version, "status": "active"},
            "meta": {"capability": "agora.dashboard.v2", "snapshot_at": utc_now(), "etag": new_etag},
        }

    # ── PATCH /bff/agora/dashboard-recipes/{recipe_id}/layout ─────────────
    @router.patch("/bff/agora/dashboard-recipes/{recipe_id}/layout")
    def patch_dashboard_recipe_layout(
        recipe_id: str,
        body: Dict[str, Any],
        response: Response,
        authorization: Optional[str] = Header(default=None),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, Any]:
        from models import ErrorCode  # noqa: PLC0415
        identity = extract_identity(authorization)
        require_read_role(identity)

        if not if_match:
            raise bff_error(400, ErrorCode.VALIDATION_FAILED, "If-Match header required", "missing_if_match")
        expected_version = body.get("expected_version")
        if not isinstance(expected_version, int):
            raise bff_error(400, ErrorCode.VALIDATION_FAILED, "expected_version (integer) required", "missing_field")
        operations: List[dict] = body.get("operations") or []
        if not operations:
            raise bff_error(400, ErrorCode.VALIDATION_FAILED, "operations array must not be empty", "missing_operations")

        ident = _recipe_identity.get(recipe_id)
        if not ident:
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, f"Recipe '{recipe_id}' not found", "recipe_not_found")

        active_version = ident["active_version"]
        cur_ver = _get_version_record(recipe_id, active_version)
        if not cur_ver:
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Version record missing", "version_not_found")

        current_etag = _make_etag(recipe_id, active_version, cur_ver["content_sha256"])
        if if_match != current_etag or expected_version != active_version:
            raise bff_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                "Dashboard recipe changed after the client snapshot.",
                "etag_mismatch",
                details_extra={
                    "expected_version": expected_version,
                    "current_version": active_version,
                    "current_etag": current_etag,
                    "latest_href": f"/bff/agora/dashboard-recipes/{recipe_id}",
                },
            )

        op_errors: List[str] = []
        for op in operations:
            op_name = op.get("op", "")
            if op_name not in _VALID_LAYOUT_OPS:
                op_errors.append(f"Unknown operation '{op_name}'")
                continue
            if op_name == "add_registered_widget":
                ws = (op.get("payload") or {}).get("widget_spec") or {}
                if ws:
                    result = _validate_widget_spec(ws)
                    if not result["valid"]:
                        op_errors.extend(e["message"] for e in result["errors"])
            if op_name == "replace_chart_spec":
                widget_id = op.get("widget_id", "")
                new_cs = (op.get("payload") or {}).get("chart_spec") or {}
                cs_kind = new_cs.get("kind")
                if widget_id and cs_kind:
                    for view in (cur_ver.get("recipe_json") or {}).get("views", []):
                        for w in (view.get("widgets") or []):
                            if w.get("widget_id") == widget_id:
                                entry = _WIDGET_REGISTRY.get(w.get("widget_type", ""))
                                if entry:
                                    allowed = entry.get("allowed_chart_kinds", [])
                                    if allowed and cs_kind not in allowed:
                                        op_errors.append(
                                            f"Chart kind '{cs_kind}' not allowed for widget '{widget_id}'"
                                        )

        if op_errors:
            raise bff_error(
                400,
                ErrorCode.VALIDATION_FAILED,
                f"Operation validation failed: {'; '.join(op_errors)}",
                "invalid_operation",
            )

        now = utc_now()
        new_version = active_version + 1
        new_recipe = _apply_layout_ops(cur_ver["recipe_json"], operations)
        new_recipe["version"] = new_version
        new_recipe["previous_version"] = active_version
        new_recipe["status"] = "active"
        new_recipe["change_reason"] = body.get("reason") or "Layout patch"
        new_recipe["updated_at"] = now
        new_recipe.pop("content_sha256", None)
        new_sha256 = _sha256(new_recipe)
        new_recipe["content_sha256"] = new_sha256

        _recipe_versions[(recipe_id, new_version)] = {
            "recipe_id": recipe_id,
            "version": new_version,
            "previous_version": active_version,
            "status": "active",
            "recipe_json": new_recipe,
            "content_sha256": new_sha256,
            "generated_by": "user",
            "change_reason": body.get("reason") or "Layout patch",
            "created_at": now,
        }
        _recipe_identity[recipe_id]["active_version"] = new_version

        if idempotency_key:
            _seen_idempotency_keys.add(idempotency_key)

        new_etag = _make_etag(recipe_id, new_version, new_sha256)
        response.headers["ETag"] = new_etag

        return {
            "data": {"recipe_id": recipe_id, "version": new_version, "status": "active"},
            "meta": {"capability": "agora.dashboard.v2", "snapshot_at": utc_now(), "etag": new_etag},
        }

    # ── POST /bff/agora/dashboard-recipes/{recipe_id}/rollback ────────────
    @router.post("/bff/agora/dashboard-recipes/{recipe_id}/rollback")
    def rollback_dashboard_recipe(
        recipe_id: str,
        body: Dict[str, Any],
        response: Response,
        authorization: Optional[str] = Header(default=None),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, Any]:
        from models import ErrorCode  # noqa: PLC0415
        identity = extract_identity(authorization)
        require_read_role(identity)

        if not if_match:
            raise bff_error(400, ErrorCode.VALIDATION_FAILED, "If-Match header required", "missing_if_match")
        expected_version = body.get("expected_version")
        target_version = body.get("target_version")
        if not isinstance(expected_version, int) or not isinstance(target_version, int):
            raise bff_error(400, ErrorCode.VALIDATION_FAILED, "expected_version and target_version (integers) required", "missing_field")

        ident = _recipe_identity.get(recipe_id)
        if not ident:
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, f"Recipe '{recipe_id}' not found", "recipe_not_found")

        active_version = ident["active_version"]
        cur_ver = _get_version_record(recipe_id, active_version)
        if not cur_ver:
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Version record missing", "version_not_found")

        current_etag = _make_etag(recipe_id, active_version, cur_ver["content_sha256"])
        if if_match != current_etag or expected_version != active_version:
            raise bff_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                "Dashboard recipe changed after the client snapshot.",
                "etag_mismatch",
                details_extra={
                    "expected_version": expected_version,
                    "current_version": active_version,
                    "current_etag": current_etag,
                    "latest_href": f"/bff/agora/dashboard-recipes/{recipe_id}",
                },
            )

        target_ver = _get_version_record(recipe_id, target_version)
        if not target_ver:
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                f"Target version {target_version} not found",
                "target_version_not_found",
            )

        now = utc_now()
        new_version = active_version + 1
        restored = copy.deepcopy(target_ver["recipe_json"])
        restored["version"] = new_version
        restored["previous_version"] = active_version
        restored["status"] = "rolled_back"
        restored["change_reason"] = body.get("reason") or f"Rollback to version {target_version}"
        restored["updated_at"] = now
        restored.pop("content_sha256", None)
        new_sha256 = _sha256(restored)
        restored["content_sha256"] = new_sha256

        _recipe_versions[(recipe_id, new_version)] = {
            "recipe_id": recipe_id,
            "version": new_version,
            "previous_version": active_version,
            "status": "rolled_back",
            "recipe_json": restored,
            "content_sha256": new_sha256,
            "generated_by": "user",
            "change_reason": body.get("reason") or f"Rollback to version {target_version}",
            "created_at": now,
        }
        _recipe_identity[recipe_id]["active_version"] = new_version

        if idempotency_key:
            _seen_idempotency_keys.add(idempotency_key)

        new_etag = _make_etag(recipe_id, new_version, new_sha256)
        response.headers["ETag"] = new_etag

        return {
            "data": {
                "recipe_id": recipe_id,
                "version": new_version,
                "status": "rolled_back",
                "rolled_back_to_version": target_version,
            },
            "meta": {"capability": "agora.dashboard.v2", "snapshot_at": utc_now(), "etag": new_etag},
        }

    # ── POST /bff/agora/dashboard-recipes/{recipe_id}/feedback ────────────
    @router.post("/bff/agora/dashboard-recipes/{recipe_id}/feedback", status_code=202)
    def submit_dashboard_recipe_feedback(
        recipe_id: str,
        body: Dict[str, Any],
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        from models import ErrorCode  # noqa: PLC0415
        identity = extract_identity(authorization)
        require_read_role(identity)

        if recipe_id not in _recipe_identity:
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, f"Recipe '{recipe_id}' not found", "recipe_not_found")

        rating = body.get("rating", "")
        context = body.get("context", "")
        if rating not in {"helpful", "neutral", "not_helpful"}:
            raise bff_error(400, ErrorCode.VALIDATION_FAILED, f"Invalid rating '{rating}'", "invalid_rating")
        if context not in {"after_accept", "after_use", "periodic_review"}:
            raise bff_error(400, ErrorCode.VALIDATION_FAILED, f"Invalid context '{context}'", "invalid_context")

        _recipe_feedback.append({
            "recipe_id": recipe_id,
            "rating": rating,
            "comment": body.get("comment") or "",
            "context": context,
            "source_version": body.get("source_version"),
            "created_at": utc_now(),
        })
        return {
            "data": {"status": "recorded"},
            "meta": {"capability": "agora.dashboard.v2", "snapshot_at": utc_now()},
        }

    # ── GET /bff/agora/dashboard-recipes/{recipe_id}/versions ─────────────
    @router.get("/bff/agora/dashboard-recipes/{recipe_id}/versions")
    def list_dashboard_recipe_versions(
        recipe_id: str,
        authorization: Optional[str] = Header(default=None),
        cursor: Optional[str] = Query(default=None),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> Dict[str, Any]:
        from models import ErrorCode  # noqa: PLC0415
        identity = extract_identity(authorization)
        require_read_role(identity)

        if recipe_id not in _recipe_identity:
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, f"Recipe '{recipe_id}' not found", "recipe_not_found")

        versions = sorted(
            [v for (rid, _ver), v in _recipe_versions.items() if rid == recipe_id],
            key=lambda v: v["version"],
        )
        if cursor:
            try:
                cursor_v = int(cursor)
                versions = [v for v in versions if v["version"] > cursor_v]
            except ValueError:
                pass

        page = versions[:limit]
        next_cursor = str(page[-1]["version"]) if len(page) == limit else None

        return {
            "data": [
                {
                    "version": v["version"],
                    "previous_version": v.get("previous_version"),
                    "status": v["status"],
                    "content_sha256": v["content_sha256"],
                    "generated_by": v.get("generated_by"),
                    "change_reason": v.get("change_reason"),
                    "created_at": v["created_at"],
                }
                for v in page
            ],
            "meta": {
                "snapshot_at": utc_now(),
                "capability": "agora.dashboard.v2",
                "recipe_id": recipe_id,
                "next_cursor": next_cursor,
            },
        }

    # ── POST /bff/agora/widgets/validate ──────────────────────────────────
    @router.post("/bff/agora/widgets/validate")
    def validate_agora_widget(
        body: Dict[str, Any],
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        from models import ErrorCode  # noqa: PLC0415
        identity = extract_identity(authorization)
        require_read_role(identity)

        result = _validate_widget_spec(body)
        if not result["valid"]:
            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "WidgetSpec v2 validation failed",
                "widget_spec_invalid",
                details_extra={
                    "errors": result["errors"],
                    "warnings": result["warnings"],
                    "registry_version": result["registry_version"],
                },
            )

        return {
            "data": result,
            "meta": {"capability": "agora.dashboard.v2", "snapshot_at": utc_now()},
        }

    # ── POST /bff/agora/widgets/{widget_id}/feedback ──────────────────────
    @router.post("/bff/agora/widgets/{widget_id}/feedback", status_code=202)
    def submit_widget_feedback(
        widget_id: str,
        body: Dict[str, Any],
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        from models import ErrorCode  # noqa: PLC0415
        identity = extract_identity(authorization)
        require_read_role(identity)

        if widget_id not in _WIDGET_REGISTRY:
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, f"Widget type '{widget_id}' not found in registry", "widget_not_found")

        rating = body.get("rating", "")
        context = body.get("context", "")
        if rating not in {"helpful", "neutral", "not_helpful"}:
            raise bff_error(400, ErrorCode.VALIDATION_FAILED, f"Invalid rating '{rating}'", "invalid_rating")
        if context not in {"after_use", "after_resize", "after_remove", "periodic_review"}:
            raise bff_error(400, ErrorCode.VALIDATION_FAILED, f"Invalid context '{context}'", "invalid_context")

        _widget_feedback.append({
            "widget_id": widget_id,
            "rating": rating,
            "comment": body.get("comment") or "",
            "context": context,
            "recipe_id": body.get("recipe_id"),
            "recipe_version": body.get("recipe_version"),
            "created_at": utc_now(),
        })
        return {
            "data": {"status": "recorded"},
            "meta": {"capability": "agora.dashboard.v2", "snapshot_at": utc_now()},
        }

    # ── POST /bff/agora/widgets/propose-plugin ────────────────────────────
    @router.post("/bff/agora/widgets/propose-plugin", status_code=202)
    def propose_widget_plugin(
        body: Dict[str, Any],
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        from models import ErrorCode  # noqa: PLC0415
        identity = extract_identity(authorization)
        require_read_role(identity)

        proposed_widget_type = body.get("proposed_widget_type", "")
        description = body.get("description", "")
        data_source_requirements: List[str] = body.get("data_source_requirements") or []

        if not proposed_widget_type:
            raise bff_error(400, ErrorCode.VALIDATION_FAILED, "proposed_widget_type required", "missing_field")
        if not description:
            raise bff_error(400, ErrorCode.VALIDATION_FAILED, "description required", "missing_field")
        if not data_source_requirements:
            raise bff_error(400, ErrorCode.VALIDATION_FAILED, "data_source_requirements must be non-empty", "missing_field")

        _plugin_proposals.append({
            "proposed_widget_type": proposed_widget_type,
            "description": description,
            "data_source_requirements": data_source_requirements,
            "chart_type_preference": body.get("chart_type_preference"),
            "example_use_case": body.get("example_use_case"),
            "created_at": utc_now(),
        })
        return {
            "data": {"status": "recorded", "proposed_widget_type": proposed_widget_type},
            "meta": {"capability": "agora.dashboard.v2", "snapshot_at": utc_now()},
        }

    return router
