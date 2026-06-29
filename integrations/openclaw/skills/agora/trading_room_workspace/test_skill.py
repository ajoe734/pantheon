"""Tests for the Trading Room workspace generator skill."""
from __future__ import annotations

from typing import Any

from integrations.openclaw.skills.agora.trading_room_workspace.skill import (
    WorkspaceGenerationInput,
    generate_trading_room_workspace_proposal,
)


def _registry() -> dict[str, dict[str, Any]]:
    return {
        "candidate_table": {
            "renderer": "chart_spec",
            "allowed_chart_kinds": ["table", "bar"],
            "allowed_data_sources": ["agora.candidate.members"],
            "allowed_interactions": ["open_candidate", "request_widget_revision"],
            "sensitivity": "user_private",
            "status": "active",
        },
        "unsupported_graph": {
            "renderer": "custom_react",
            "allowed_chart_kinds": ["network"],
            "allowed_data_sources": ["agora.candidate.members"],
            "allowed_interactions": ["open_candidate", "request_widget_revision"],
            "sensitivity": "user_private",
            "status": "active",
        },
    }


def _widget(**overrides: Any) -> dict[str, Any]:
    widget = {
        "id": "candidate_table",
        "widgetType": "candidate_table",
        "title": "Candidate Table",
        "purpose": "Compare candidates.",
        "whyIncluded": "Required for candidate review.",
        "dataSource": "agora.candidate.members",
        "query": {"filters": {}, "sort": {}, "limit": 100, "window": "20d"},
        "chartSpec": {
            "spec_version": "1.0",
            "kind": "table",
            "encodings": {},
            "transforms": [],
            "tooltip_fields": [],
            "thresholds": [],
            "click_action": {"kind": "request_widget_revision"},
            "options": {},
        },
        "interactions": [{"kind": "open_candidate"}, {"kind": "request_widget_revision"}],
        "placement": {"x": 0, "y": 0, "width": 4, "height": 3, "minWidth": 2, "minHeight": 2},
        "minSize": {"width": 2, "height": 2},
        "maxSize": {"width": 12, "height": 8},
        "sensitivity": "user_private",
        "visible": True,
    }
    widget.update(overrides)
    return widget


def _view_factory(widget: dict[str, Any]):
    def _factory(_input_data: WorkspaceGenerationInput) -> list[dict[str, Any]]:
        return [
            {
                "id": "test_view",
                "title": "Test View",
                "purpose": "Test generated view.",
                "order": 1,
                "layoutTemplate": "test_grid",
                "widgets": [widget],
            }
        ]

    return _factory


def _no_extra_errors(_widget: dict[str, Any]) -> list[str]:
    return []


def _input(**overrides: Any) -> WorkspaceGenerationInput:
    base = {
        "strategy_id": "strat-wb",
        "strategy_version": "V4",
        "proposal_id": "trp-test",
        "generated_at": "2026-06-29T00:00:00Z",
        "trading_room_ready": True,
    }
    base.update(overrides)
    return WorkspaceGenerationInput(**base)


def test_generator_preserves_evidence_freshness_and_filters_unsafe_personalization():
    result = generate_trading_room_workspace_proposal(
        _input(
            evidence_refs=["ev-001"],
            data_freshness={
                "agora.candidate.members": {
                    "status": "complete",
                    "dataCutoff": "2026-06-28T23:00:00Z",
                }
            },
            personalization_hints={"density": "compact", "javascript": "alert(1)"},
        ),
        view_factory=_view_factory(_widget()),
        widget_registry=_registry(),
        validate_widget=_no_extra_errors,
        required_view_ids=("test_view",),
    )

    assert result.status == "completed"
    assert result.proposal is not None
    proposal = result.proposal
    assert proposal["personalizationApplied"]["items"] == [{"key": "density", "value": "compact"}]
    assert "Unsafe personalization hints ignored" in proposal["warnings"][-1]
    source = proposal["dataAvailability"]["sources"][0]
    assert source["status"] == "complete"
    assert "ev-001" in source["reason"]
    assert "2026-06-28T23:00:00Z" in source["reason"]
    assert result.meta()["evidenceRefs"] == ["ev-001"]


def test_generator_uses_supported_fallback_for_unsupported_renderer():
    result = generate_trading_room_workspace_proposal(
        _input(),
        view_factory=_view_factory(
            _widget(
                widgetType="unsupported_graph",
                chartSpec={**_widget()["chartSpec"], "kind": "network"},
            )
        ),
        widget_registry=_registry(),
        validate_widget=_no_extra_errors,
        required_view_ids=("test_view",),
        renderer_fallbacks={
            "unsupported_graph": {
                "widgetType": "candidate_table",
                "chartKind": "table",
                "title": "Candidate Table Fallback",
                "whyIncluded": "Existing table fallback for unsupported renderer.",
            }
        },
    )

    assert result.status == "completed"
    assert result.proposal is not None
    widget = result.proposal["views"][0]["widgets"][0]
    assert widget["widgetType"] == "candidate_table"
    assert widget["chartSpec"]["kind"] == "table"
    assert result.supported_fallbacks[0]["originalWidgetType"] == "unsupported_graph"
    assert result.component_task_requests == []


def test_generator_returns_component_task_request_without_fallback():
    result = generate_trading_room_workspace_proposal(
        _input(),
        view_factory=_view_factory(
            _widget(
                widgetType="unsupported_graph",
                chartSpec={**_widget()["chartSpec"], "kind": "network"},
            )
        ),
        widget_registry=_registry(),
        validate_widget=_no_extra_errors,
        required_view_ids=("test_view",),
    )

    assert result.status == "blocked"
    assert result.proposal is None
    assert result.component_task_requests[0]["requestedWidgetType"] == "unsupported_graph"
    assert result.component_task_requests[0]["componentTaskType"] == "frontend_widget_renderer"


def test_generator_blocks_bad_data_source_and_executable_content():
    bad_widget = _widget(
        dataSource="https://example.invalid/raw",
        chartSpec={**_widget()["chartSpec"], "options": {"formatter": "<script>alert(1)</script>"}},
    )
    result = generate_trading_room_workspace_proposal(
        _input(),
        view_factory=_view_factory(bad_widget),
        widget_registry=_registry(),
        validate_widget=_no_extra_errors,
        required_view_ids=("test_view",),
    )

    assert result.status == "blocked"
    assert any("data source" in error for error in result.validation_errors)
    assert any("forbidden executable content" in error for error in result.validation_errors)
