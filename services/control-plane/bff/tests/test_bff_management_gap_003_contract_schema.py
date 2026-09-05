"""MGMT-GAP-003 OpenAPI contract hardening for management console reads."""
from __future__ import annotations

import os
import sys

from fastapi.testclient import TestClient


from services.control_plane.bff import main as bff_main


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


def _response_schema_ref(schema: dict, path: str) -> str:
    response_schema = schema["paths"][path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    if "$ref" in response_schema:
        return response_schema["$ref"].rsplit("/", 1)[-1]
    if "allOf" in response_schema and response_schema["allOf"]:
        return response_schema["allOf"][0]["$ref"].rsplit("/", 1)[-1]
    raise AssertionError(f"{path} does not publish a component response schema: {response_schema}")


def test_mgmt_gap_003_management_reads_publish_typed_openapi_envelopes() -> None:
    bff_main.app.openapi_schema = None
    schema = TestClient(bff_main.app).get("/openapi.json").json()

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
    bff_main.app.openapi_schema = None
    schema = bff_main.app.openapi()
    surface_schema = schema["components"]["schemas"]["SurfaceState"]

    assert surface_schema["required"][:2] == ["status", "source"]
    assert set(surface_schema["properties"]["status"]["enum"]) == {"ok", "degraded", "unavailable"}
