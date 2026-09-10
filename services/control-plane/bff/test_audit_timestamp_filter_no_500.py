"""Regression: audit time-range filters must not 500 on any from_ts/to_ts value.

Verification campaign 2026-06-14, round 16, finding F12. The audit events/export
handlers called , which was defined nowhere — a NameError
on every non-empty from_ts/to_ts (even a valid timestamp), surfacing as 500. The
same missing-symbol affected  call sites
(, aggregated-recency). Fixed by defining
 in incidents service and repointing the calls.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.auth.policy import create_auth_dependencies
from services.control_plane.bff.incidents.router import create_incident_router
from services.control_plane.bff.incidents.service import _parse_rfc3339
from services.control_plane.bff.ports import ReadSurfacePorts

os.environ.setdefault("PANTHEON_BFF_AUTH_STUB", "true")
os.environ.setdefault("PANTHEON_BFF_AUTH_MODE", "permissive")

deps = create_auth_dependencies()
store = ReadSurfacePorts()
app = FastAPI()
app.include_router(
    create_incident_router(
        read_surface=store,
        get_read_store=lambda: store,
        extract_identity=deps.extract_identity,
        require_read_role=deps.require_read_role,
    )
)

CLIENT = TestClient(app)
HEADERS = {"Authorization": "Bearer op-audit:operator,admin,reviewer:mfa"}


def test_parse_rfc3339_is_defined_and_safe():
    assert _parse_rfc3339("2026-01-01T00:00:00Z") is not None
    assert _parse_rfc3339("-1") is None
    assert _parse_rfc3339("") is None
    assert _parse_rfc3339(None) is None


@pytest.mark.parametrize(
    "query",
    [
        "",
        "?from_ts=2026-01-01T00:00:00Z",
        "?from_ts=-1",
        "?to_ts=garbage",
        "?from_ts=2026-01-01T00:00:00Z&to_ts=2026-12-31T23:59:59Z",
    ],
)
def test_audit_events_timestamp_filter_never_500(query):
    r = CLIENT.get("/bff/audit/events" + query, headers=HEADERS)
    assert r.status_code != 500, r.text
    assert r.status_code == 200


@pytest.mark.parametrize("query", ["?from_ts=-1", "?to_ts=bad", "?from_ts=2026-01-01T00:00:00Z"])
def test_audit_export_timestamp_filter_never_500(query):
    r = CLIENT.get("/bff/audit/export" + query, headers=HEADERS)
    assert r.status_code != 500, r.text
