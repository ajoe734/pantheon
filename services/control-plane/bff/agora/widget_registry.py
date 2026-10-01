"""Agora widget registry and chart grammar validator.

Extracted from dashboard router to support trading-room workspace validation
independently of deleted dashboard routes.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from typing import Any, Dict, List, Optional, Tuple

# ── Widget registry ──────────────────────────────────────────────────────────

_WIDGET_REGISTRY_PATH = os.path.normpath(os.path.join(
    os.path.dirname(__file__),
    "..", "..",
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
        return {
            "valid": False,
            "errors": errors,
            "warnings": warnings,
            "registry_version": _REGISTRY_VERSION,
            "schema_hash": _REGISTRY_SCHEMA_HASH,
        }

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
