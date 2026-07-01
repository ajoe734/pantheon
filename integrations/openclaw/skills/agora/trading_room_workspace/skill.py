"""agora-trading-room-workspace skill.

Builds the V11 TradingRoomWorkspaceProposal from a ready StrategySpec version.
The skill only emits declarative TradingRoomWidgetSpec / ChartSpec payloads and
validates them against the canonical widget registry. Unsupported renderers are
reported as supported fallbacks or component task requests; executable frontend
code is never generated.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence


WINNER_BRANCH_VIEW_IDS = (
    "strategy_overview",
    "candidates_entry",
    "winner_branch_intelligence",
    "related_party_flow_migration",
    "event_lead",
    "positions_exit",
    "evidence_monitoring",
)

SUPPORTED_RENDERERS = frozenset({"chart_spec"})
FORBIDDEN_PERSONALIZATION_KEYS = frozenset(
    {"rawHtml", "raw_html", "javascript", "react", "html", "rawPrompt", "raw_prompt"}
)
FORBIDDEN_CONTENT_PATTERNS = (
    "<script",
    "javascript:",
    "data:text/html",
    "dangerouslysetinnerhtml",
    "eval(",
    "new function",
)

BASE_PROPOSAL_WARNINGS = (
    "Workspace is decision-support only; it cannot route orders, bind capital, or mutate runtime bindings.",
    "Relationship, migration, and event-lead widgets represent evidence strength and statistical association only.",
)


@dataclass(frozen=True)
class WorkspaceGenerationInput:
    strategy_id: str
    strategy_version: str
    proposal_id: str
    generated_at: str
    personalization_hints: Mapping[str, Any] = field(default_factory=dict)
    evidence_refs: Sequence[str] = field(default_factory=tuple)
    data_freshness: Mapping[str, Any] = field(default_factory=dict)
    trading_room_ready: bool = True


@dataclass(frozen=True)
class WorkspaceGenerationResult:
    status: str
    proposal: dict[str, Any] | None = None
    blocking_reasons: list[str] = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)
    validation_warnings: list[str] = field(default_factory=list)
    supported_fallbacks: list[dict[str, Any]] = field(default_factory=list)
    component_task_requests: list[dict[str, Any]] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    data_freshness: dict[str, Any] = field(default_factory=dict)

    def meta(self) -> dict[str, Any]:
        return {
            "skill": "agora-trading-room-workspace",
            "status": self.status,
            "evidenceRefs": self.evidence_refs,
            "dataFreshness": self.data_freshness,
            "supportedFallbacks": self.supported_fallbacks,
            "componentTaskRequests": self.component_task_requests,
            "validationWarnings": self.validation_warnings,
            "blockingReasons": self.blocking_reasons,
        }


ViewFactory = Callable[[WorkspaceGenerationInput], Sequence[Mapping[str, Any]]]
WidgetValidator = Callable[[dict[str, Any]], Sequence[str]]


def generate_trading_room_workspace_proposal(
    input_data: WorkspaceGenerationInput,
    *,
    view_factory: ViewFactory,
    widget_registry: Mapping[str, Mapping[str, Any]],
    validate_widget: WidgetValidator,
    required_view_ids: Sequence[str] = WINNER_BRANCH_VIEW_IDS,
    renderer_fallbacks: Mapping[str, Mapping[str, Any]] | None = None,
) -> WorkspaceGenerationResult:
    """Generate a complete, registry-validated TradingRoomWorkspaceProposal.

    The route layer supplies a controlled view_factory for the strategy family.
    This skill enforces the servant-runtime boundary: every widget is checked
    against the registry and the route validator before the proposal is returned.
    """
    evidence_refs = _normalized_string_list(input_data.evidence_refs)
    data_freshness = dict(input_data.data_freshness or {})
    if not input_data.trading_room_ready:
        return WorkspaceGenerationResult(
            status="blocked",
            blocking_reasons=["strategy version is not ready for Trading Room workspace generation"],
            evidence_refs=evidence_refs,
            data_freshness=data_freshness,
        )

    validation_errors: list[str] = []
    validation_warnings: list[str] = []
    supported_fallbacks: list[dict[str, Any]] = []
    component_task_requests: list[dict[str, Any]] = []
    renderer_fallbacks = renderer_fallbacks or {}

    views = [copy.deepcopy(dict(view)) for view in view_factory(input_data)]
    actual_view_ids = tuple(str(view.get("id") or "") for view in views)
    if actual_view_ids != tuple(required_view_ids):
        validation_errors.append(
            f"proposal must include the full V11 Winner Branch view set; got {list(actual_view_ids)}"
        )

    for view_index, view in enumerate(views):
        widgets = view.get("widgets") or []
        if not isinstance(widgets, list):
            validation_errors.append(f"views[{view_index}].widgets must be an array")
            continue
        resolved_widgets: list[dict[str, Any]] = []
        for widget_index, raw_widget in enumerate(widgets):
            path = f"views[{view_index}].widgets[{widget_index}]"
            if not isinstance(raw_widget, dict):
                validation_errors.append(f"{path} must be an object")
                continue
            resolved = _resolve_widget_renderer(
                raw_widget,
                widget_registry=widget_registry,
                renderer_fallbacks=renderer_fallbacks,
                supported_fallbacks=supported_fallbacks,
                component_task_requests=component_task_requests,
                path=path,
            )
            if resolved is None:
                continue
            errors = _validate_widget_registry_rules(
                resolved,
                widget_registry=widget_registry,
                path=path,
            )
            errors.extend(str(error) for error in validate_widget(resolved))
            if errors:
                validation_errors.extend(errors)
                continue
            resolved_widgets.append(resolved)
        view["widgets"] = resolved_widgets
        view["widgetCount"] = len(resolved_widgets)
        view.setdefault("warnings", [])
        view.setdefault("dataAvailability", "partial")

    if component_task_requests:
        validation_warnings.append(
            "One or more requested widget renderers need a new component task before production rendering."
        )
    if validation_errors or component_task_requests:
        return WorkspaceGenerationResult(
            status="blocked",
            proposal=None,
            validation_errors=validation_errors,
            validation_warnings=validation_warnings,
            component_task_requests=component_task_requests,
            supported_fallbacks=supported_fallbacks,
            evidence_refs=evidence_refs,
            data_freshness=data_freshness,
        )

    personalization, personalization_warnings = _sanitize_personalization(
        input_data.personalization_hints
    )
    validation_warnings.extend(personalization_warnings)
    data_availability = _build_data_availability(
        views,
        evidence_refs=evidence_refs,
        data_freshness=data_freshness,
    )

    proposal = {
        "strategyId": input_data.strategy_id,
        "strategyVersion": input_data.strategy_version,
        "proposalId": input_data.proposal_id,
        "generatedAt": input_data.generated_at,
        "status": "preview",
        "views": views,
        "rationale": (
            "Generated from the V11 Winner Branch Trading Room contract: seven strategy-specific "
            "views covering overview, entry, branch intelligence, migration, event lead, positions, and evidence."
        ),
        "dataAvailability": data_availability,
        "warnings": list(BASE_PROPOSAL_WARNINGS) + validation_warnings,
        "personalizationApplied": {
            "status": "applied" if personalization else "not_applied",
            "items": [
                {"key": str(key), "value": value}
                for key, value in sorted(personalization.items())
            ],
        },
    }
    return WorkspaceGenerationResult(
        status="completed",
        proposal=proposal,
        validation_warnings=validation_warnings,
        supported_fallbacks=supported_fallbacks,
        evidence_refs=evidence_refs,
        data_freshness=data_freshness,
    )


def _resolve_widget_renderer(
    widget: Mapping[str, Any],
    *,
    widget_registry: Mapping[str, Mapping[str, Any]],
    renderer_fallbacks: Mapping[str, Mapping[str, Any]],
    supported_fallbacks: list[dict[str, Any]],
    component_task_requests: list[dict[str, Any]],
    path: str,
) -> dict[str, Any] | None:
    widget_type = str(widget.get("widgetType") or "")
    entry = widget_registry.get(widget_type)
    renderer = str((entry or {}).get("renderer") or "")
    if entry and renderer in SUPPORTED_RENDERERS:
        return copy.deepcopy(dict(widget))

    fallback = renderer_fallbacks.get(widget_type)
    if fallback:
        candidate = _apply_fallback(widget, fallback)
        supported_fallbacks.append(
            {
                "path": path,
                "originalWidgetType": widget_type,
                "fallbackWidgetType": candidate.get("widgetType"),
                "fallbackChartKind": (candidate.get("chartSpec") or {}).get("kind"),
                "reason": f"renderer {renderer or '<missing>'} is not supported by the Trading Room renderer allowlist",
            }
        )
        return candidate

    component_task_requests.append(
        {
            "path": path,
            "requestedWidgetType": widget_type,
            "requestedRenderer": renderer or "<missing>",
            "reason": "No supported ChartSpec fallback is registered for this widget renderer.",
            "componentTaskType": "frontend_widget_renderer",
        }
    )
    return None


def _apply_fallback(widget: Mapping[str, Any], fallback: Mapping[str, Any]) -> dict[str, Any]:
    candidate = copy.deepcopy(dict(widget))
    if fallback.get("widgetType"):
        candidate["widgetType"] = fallback["widgetType"]
    if fallback.get("title"):
        candidate["title"] = fallback["title"]
    if fallback.get("purpose"):
        candidate["purpose"] = fallback["purpose"]
    if fallback.get("whyIncluded"):
        candidate["whyIncluded"] = fallback["whyIncluded"]
    else:
        candidate["whyIncluded"] = (
            f"{candidate.get('whyIncluded', '')} Supported renderer fallback applied.".strip()
        )
    if fallback.get("dataSource"):
        candidate["dataSource"] = fallback["dataSource"]
    chart_spec = copy.deepcopy(candidate.get("chartSpec") or {})
    if fallback.get("chartKind"):
        chart_spec["kind"] = fallback["chartKind"]
    chart_spec.setdefault("spec_version", "1.0")
    chart_spec.setdefault("encodings", {})
    chart_spec.setdefault("transforms", [])
    chart_spec.setdefault("tooltip_fields", [])
    chart_spec.setdefault("thresholds", [])
    chart_spec.setdefault("click_action", {"kind": "request_widget_revision"})
    chart_spec.setdefault("options", {})
    candidate["chartSpec"] = chart_spec
    return candidate


def _validate_widget_registry_rules(
    widget: Mapping[str, Any],
    *,
    widget_registry: Mapping[str, Mapping[str, Any]],
    path: str,
) -> list[str]:
    errors: list[str] = []
    widget_type = str(widget.get("widgetType") or "")
    entry = widget_registry.get(widget_type)
    if not entry:
        return [f"{path}: widget type {widget_type!r} not found in widget_registry.v1"]
    if entry.get("status") != "active":
        errors.append(f"{path}: widget type {widget_type!r} is not active")
    renderer = str(entry.get("renderer") or "")
    if renderer not in SUPPORTED_RENDERERS:
        errors.append(f"{path}: renderer {renderer!r} is not supported")

    chart_kind = str((widget.get("chartSpec") or {}).get("kind") or "")
    allowed_chart_kinds = set(entry.get("allowed_chart_kinds") or [])
    if chart_kind not in allowed_chart_kinds:
        errors.append(
            f"{path}: chart kind {chart_kind!r} is not allowed for widget type {widget_type!r}"
        )

    data_source = str(widget.get("dataSource") or "")
    allowed_data_sources = set(entry.get("allowed_data_sources") or [])
    if data_source not in allowed_data_sources:
        errors.append(
            f"{path}: data source {data_source!r} is not allowed for widget type {widget_type!r}"
        )

    allowed_interactions = set(entry.get("allowed_interactions") or [])
    for interaction in widget.get("interactions") or []:
        kind = interaction.get("kind") if isinstance(interaction, dict) else None
        if kind not in allowed_interactions:
            errors.append(
                f"{path}: interaction {kind!r} is not allowed for widget type {widget_type!r}"
            )

    forbidden = _first_forbidden_content(widget)
    if forbidden:
        errors.append(f"{path}: forbidden executable content pattern {forbidden!r} detected")
    return errors


def _first_forbidden_content(value: Any) -> str | None:
    try:
        serialized = json.dumps(value, sort_keys=True, ensure_ascii=False).lower()
    except TypeError:
        serialized = str(value).lower()
    for pattern in FORBIDDEN_CONTENT_PATTERNS:
        if pattern in serialized:
            return pattern
    return None


def _sanitize_personalization(
    personalization_hints: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    sanitized: dict[str, Any] = {}
    dropped: list[str] = []
    for key, value in (personalization_hints or {}).items():
        key_text = str(key)
        if key_text in FORBIDDEN_PERSONALIZATION_KEYS or _first_forbidden_content(value):
            dropped.append(key_text)
            continue
        sanitized[key_text] = value
    warnings = []
    if dropped:
        warnings.append(f"Unsafe personalization hints ignored: {sorted(dropped)}")
    return sanitized, warnings


def _build_data_availability(
    views: Sequence[Mapping[str, Any]],
    *,
    evidence_refs: Sequence[str],
    data_freshness: Mapping[str, Any],
) -> dict[str, Any]:
    ordered_sources: list[str] = []
    for view in views:
        for widget in view.get("widgets") or []:
            data_source = str(widget.get("dataSource") or "")
            if data_source and data_source not in ordered_sources:
                ordered_sources.append(data_source)

    source_rows = [
        _source_availability(source, evidence_refs=evidence_refs, data_freshness=data_freshness)
        for source in ordered_sources
    ]
    statuses = {row["status"] for row in source_rows}
    if statuses == {"complete"}:
        status = "complete"
    elif statuses == {"unavailable"}:
        status = "unavailable"
    else:
        status = "partial"
    return {"status": status, "sources": source_rows}


def _source_availability(
    data_source: str,
    *,
    evidence_refs: Sequence[str],
    data_freshness: Mapping[str, Any],
) -> dict[str, str]:
    freshness = data_freshness.get(data_source)
    status = "partial"
    reason_parts = ["generated from ready StrategySpec version; live projection may lag research"]
    if isinstance(freshness, Mapping):
        requested_status = str(freshness.get("status") or "").strip()
        if requested_status in {"complete", "partial", "unavailable"}:
            status = requested_status
        for key in ("asOf", "as_of", "dataCutoff", "data_cutoff", "lag"):
            if freshness.get(key):
                reason_parts.append(f"{key}={freshness[key]}")
    elif freshness:
        reason_parts.append(f"freshness={freshness}")
    if evidence_refs:
        reason_parts.append("evidenceRefs=" + ",".join(evidence_refs[:5]))
    return {"dataSource": data_source, "status": status, "reason": "; ".join(reason_parts)}


def _normalized_string_list(values: Sequence[Any]) -> list[str]:
    return [str(value).strip() for value in values if str(value).strip()]
