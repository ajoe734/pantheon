"""TJ-E2E-011 operator dashboard artifact for SLO and data-quality metrics.

Review feedback on PR #3460 found no dashboard artifact existed at all.
`trade_journey_slo_dashboard.json` is that artifact: an ordered set of panels,
each naming a real `DataQualityMetrics` field. `validate_dashboard()` keeps
it honest — every panel's `metric` must be a real field, so renaming a
`DataQualityMetrics` field without updating the dashboard fails
`test_dashboard.py` instead of silently shipping a panel with no data.
`render_dashboard_snapshot()` is what makes the artifact executable rather
than decorative: it populates each panel with a live value (and, where
defined, the SLO target) from an actual `DataQualityMetrics` instance, and
the BFF SLO endpoint (`services/control-plane/bff/trade_journeys.py`) calls
it on every request.
"""
from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path
from typing import Any, Mapping

from services.trade_journey.slo_data_quality import DataQualityMetrics, SLOTargets, metrics_to_dict

DEFAULT_DASHBOARD_PATH = Path(__file__).with_name("trade_journey_slo_dashboard.json")
SCHEMA_VERSION = "trade_journey_slo_dashboard.v1"

_METRIC_FIELD_NAMES = frozenset(f.name for f in fields(DataQualityMetrics))
_TARGET_FIELD_NAMES = frozenset(f.name for f in fields(SLOTargets))


def load_dashboard(path: Path = DEFAULT_DASHBOARD_PATH) -> dict[str, Any]:
    dashboard = json.loads(path.read_text(encoding="utf-8"))
    panels = dashboard.get("panels")
    if not isinstance(panels, list) or not panels:
        raise ValueError("trade_journey_slo_dashboard.json must contain a non-empty panels list")
    return dashboard


def validate_dashboard(dashboard: Mapping[str, Any]) -> tuple[str, ...]:
    """Return panel ids that name a metric or target field that doesn't exist."""
    issues = []
    for panel in dashboard["panels"]:
        if panel.get("metric") not in _METRIC_FIELD_NAMES:
            issues.append(panel["panel_id"])
            continue
        target_field = panel.get("target_field")
        if target_field is not None and target_field not in _TARGET_FIELD_NAMES:
            issues.append(panel["panel_id"])
    return tuple(issues)


def render_dashboard_snapshot(
    dashboard: Mapping[str, Any],
    metrics: DataQualityMetrics,
    targets: SLOTargets | None = None,
) -> dict[str, Any]:
    """Populate each panel's `value` (and `target_value`, if defined) live."""
    values = metrics_to_dict(metrics)
    rendered = []
    for panel in dashboard["panels"]:
        target_value = None
        target_field = panel.get("target_field")
        if targets is not None and target_field is not None:
            multiplier = panel.get("target_unit_multiplier", 1.0)
            target_value = getattr(targets, target_field) * multiplier
        rendered.append({**panel, "value": values.get(panel["metric"]), "target_value": target_value})
    return {
        "schema_version": dashboard.get("schema_version", SCHEMA_VERSION),
        "title": dashboard.get("title"),
        "measured_at": metrics.measured_at,
        "environment": metrics.environment,
        "panels": rendered,
    }
