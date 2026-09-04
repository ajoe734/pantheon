"""Ownership checks for the dedicated Deployment BFF router."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


BFF_ROOT = Path(__file__).resolve().parents[1]
from services.control_plane.bff.deployment.router import create_deployment_router


def _page_slice(
    items: List[Dict[str, Any]],
    _page_token: Optional[str],
    _page_size: int,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    return items, None


def _build_router():
    return create_deployment_router(
        queries=object(),
        extract_identity=lambda _authorization: object(),
        require_read_role=lambda _identity: None,
        require_operator_role=lambda _identity: None,
        bff_error=lambda *_args, **_kwargs: RuntimeError("unexpected test error"),
        utc_now=lambda: "2026-08-30T00:00:00Z",
        page_slice=_page_slice,
        snapshot_meta=lambda _snapshot_at: {"snapshot_at": _snapshot_at},
        dataset_surface_status=lambda *_args, **_kwargs: {"status": "available"},
        composed_surface_status=lambda *_args, **_kwargs: {"status": "available"},
        read_surface_meta=lambda *_args, **_kwargs: {},
        raise_if_read_surface_unavailable=lambda *_args, **_kwargs: None,
        aggregate_group_surface=lambda *_args, **_kwargs: {"status": "available"},
        split_csv_query=lambda value: value.split(",") if value else None,
        meta_staleness=lambda: None,
        stable_json_hash=lambda payload: "hash",
        resolve_final_idempotency_key=lambda resolved, header: resolved or header or "key",
        reject_body_idempotency_key=lambda _payload: None,
        request_dry_run_requested=lambda *_args, **_kwargs: False,
        gov_bff_idempotency={},
        publish_event=lambda *_args, **_kwargs: "event-id",
        sse_buffers={},
        sse_subscribers={},
        gov_bff_action_command=lambda *_args, **_kwargs: {},
        deprecated_bff_path_response=lambda *_args, **_kwargs: None,
        sem_command_response=lambda *_args, **_kwargs: {},
        stream_generic_events=lambda *_args, **_kwargs: iter(()),
        surface_degradation_reason=lambda *_args, **_kwargs: None,
    )


def test_deployment_router_owns_all_deployment_routes() -> None:
    router = _build_router()

    expected = {
        ("GET", "/api/v1/deployment-plans"),
        ("POST", "/api/v1/deployment-plans"),
        ("GET", "/api/v1/deployment-plans/{plan_id}"),
        ("GET", "/api/v1/operator/deployment-plans"),
        ("GET", "/api/v1/operator/deployment-review/{plan_id}"),
        ("GET", "/api/v1/operator/deployment-diff/{plan_id}"),
        ("GET", "/bff/sse/deployment/events"),
        ("GET", "/bff/deployments"),
        ("GET", "/bff/deployments/{deployment_id}"),
        ("POST", "/bff/deployments/{deployment_id}/actions/{action_id}"),
        ("POST", "/bff/deployments"),
        ("PATCH", "/bff/deployments/{deployment_id}"),
    }
    actual = {
        (method, route.path)
        for route in router.routes
        for method in route.methods
        if method in {"GET", "POST", "PATCH"}
    }
    assert actual == expected


def test_main_composes_deployment_router_without_inline_decorators() -> None:
    main_source = (BFF_ROOT / "main.py").read_text(encoding="utf-8")
    assert "from .deployment.router import create_deployment_router" in main_source

    extracted_paths = (
        r'"/api/v1/deployment-plans"',
        r'"/api/v1/deployment-plans/\{plan_id\}"',
        r'"/api/v1/operator/deployment-plans"',
        r'"/api/v1/operator/deployment-review/\{plan_id\}"',
        r'"/api/v1/operator/deployment-diff/\{plan_id\}"',
        r'"/bff/sse/deployment/events"',
        r'"/bff/deployments"',
        r'"/bff/deployments/\{deployment_id\}"',
        r'"/bff/deployments/\{deployment_id\}/actions/\{action_id\}"',
    )
    for path_pattern in extracted_paths:
        assert not re.search(
            r"@app\.(?:get|post|put|patch|delete)\(" + path_pattern,
            main_source,
        ), f"{path_pattern} should be owned by deployment.router, not main.py"


def test_deployment_service_accepts_typed_queries() -> None:
    from services.control_plane.bff.deployment.ports import DeploymentQueries
    from services.control_plane.bff.deployment.service import DeploymentService

    mock_queries = object()
    service = DeploymentService(
        queries=mock_queries,
        bff_error=lambda *a, **k: RuntimeError(),
        dataset_surface_status=lambda *a, **k: {},
        composed_surface_status=lambda *a, **k: {},
        aggregate_group_surface=lambda *a, **k: {},
        split_csv_query=lambda *a, **k: None,
        snapshot_meta=lambda *a, **k: {},
        surface_degradation_reason=lambda *a, **k: None,
    )
    assert service.queries is mock_queries
    assert not hasattr(service, "read_store")


def test_main_composes_deployment_router_with_queries_not_closure() -> None:
    main_source = (BFF_ROOT / "main.py").read_text(encoding="utf-8")
    assert "queries=read_store" in main_source
    assert "_create_deployment_router(\n        queries=read_store," in main_source
