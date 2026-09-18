"""MGMT-GAP-003 OpenAPI contract hardening for management console reads."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.console_gap.consult_rules import create_consult_rules_router
from services.control_plane.bff.console_gap.datasources import create_datasources_router
from services.control_plane.bff.console_gap.knowledge import create_knowledge_router
from services.control_plane.bff.console_gap.lineage import create_lineage_router
from services.control_plane.bff.console_gap.memory_governance import create_memory_governance_router
from services.control_plane.bff.console_gap.permissions import create_permissions_router
from services.control_plane.bff.console_gap.workflows_hooks import create_workflows_hooks_router
from services.control_plane.bff.governance.router import _default_dataset_surface_status
from services.control_plane.bff.management_read_models.router import (
    _default_extract_identity,
    _default_require_read_role,
    _default_snapshot_meta,
    _utc_now_rfc3339,
)
from services.control_plane.bff.ports import create_in_memory_read_surface_ports


REQUIRED_ENDPOINTS = {
    "/bff/management/data-sources": "DataSourcesEnvelope",
    "/bff/management/permissions": "ManagementRecordsEnvelope",
    "/bff/management/memory-governance": "ManagementRecordsEnvelope",
    "/bff/management/consult-rules": "ManagementRecordsEnvelope",
    "/bff/lineage": "LineageEnvelope",
    "/bff/workflows": "ManagementRecordsEnvelope",
    "/bff/hooks": "ManagementRecordsEnvelope",
    "/bff/knowledge": "ManagementRecordsEnvelope",
}


def _build_app() -> FastAPI:
    app = FastAPI()
    store = create_in_memory_read_surface_ports()
    common_kwargs = dict(
        read_surface=store,
        extract_identity=_default_extract_identity,
        require_read_role=_default_require_read_role,
    )
    app.include_router(create_permissions_router(**common_kwargs))
    app.include_router(create_memory_governance_router(**common_kwargs))
    app.include_router(create_consult_rules_router(**common_kwargs))
    app.include_router(
        create_datasources_router(
            read_surface=store,
            extract_identity=_default_extract_identity,
            require_read_role=_default_require_read_role,
            snapshot_meta=_default_snapshot_meta,
            utc_now=_utc_now_rfc3339,
        )
    )
    app.include_router(
        create_lineage_router(
            read_surface=store,
            extract_identity=_default_extract_identity,
            require_read_role=_default_require_read_role,
            snapshot_meta=_default_snapshot_meta,
            utc_now=_utc_now_rfc3339,
        )
    )
    app.include_router(
        create_workflows_hooks_router(
            workflow_hook_port=store,
            extract_identity=_default_extract_identity,
            require_read_role=_default_require_read_role,
            snapshot_now=_utc_now_rfc3339,
        )
    )
    app.include_router(
        create_knowledge_router(
            extract_identity=_default_extract_identity,
            require_read_role=_default_require_read_role,
            port=store,
            utc_now=_utc_now_rfc3339,
            dataset_surface_status=_default_dataset_surface_status,
        )
    )
    return app


def _response_schema_ref(schema: dict, path: str) -> str:
    response_schema = schema["paths"][path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    if "$ref" in response_schema:
        return response_schema["$ref"].rsplit("/", 1)[-1]
    if "allOf" in response_schema and response_schema["allOf"]:
        return response_schema["allOf"][0]["$ref"].rsplit("/", 1)[-1]
    raise AssertionError(f"{path} does not publish a component response schema: {response_schema}")


def test_mgmt_gap_003_management_reads_publish_typed_openapi_envelopes() -> None:
    app = _build_app()
    schema = TestClient(app).get("/openapi.json").json()

    components = schema["components"]["schemas"]
    for component_name in (
        "SurfaceState",
        "PageInfo",
        "ManagementListMeta",
        "ManagementRecordsEnvelope",
        "DataSourcesEnvelope",
        "LineageEnvelope",
    ):
        assert component_name in components

    for path, expected_component in REQUIRED_ENDPOINTS.items():
        assert path in schema["paths"]
        assert _response_schema_ref(schema, path) == expected_component


def test_mgmt_gap_003_surface_schema_requires_status_and_source() -> None:
    app = _build_app()
    schema = app.openapi()
    surface_schema = schema["components"]["schemas"]["SurfaceState"]

    assert surface_schema["required"][:2] == ["status", "source"]
    assert set(surface_schema["properties"]["status"]["enum"]) == {"ok", "degraded", "unavailable"}
