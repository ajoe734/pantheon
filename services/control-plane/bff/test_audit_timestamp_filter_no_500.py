"""Regression: audit time-range filters must not 500 on any from_ts/to_ts value.

Verification campaign 2026-06-14, round 16, finding F12. The audit events/export
handlers called `_parse_rfc3339_header`, which was defined nowhere — a NameError
on every non-empty from_ts/to_ts (even a valid timestamp), surfacing as 500. The
same missing-symbol affected `_parse_rfc3339` call sites
(`_kw04_within_recency`, aggregated-recency). Fixed by defining
`_parse_rfc3339` in main and repointing the calls.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.auth.policy import extract_identity_stub
from services.control_plane.bff.incidents.router import _parse_rfc3339, create_incident_router
from services.control_plane.bff.incidents.service import IncidentService

HEADERS = {"Authorization": "Bearer op-audit:operator,admin,reviewer:mfa"}


class _AuditReadStore:
    def list_governance_audit_events(
        self,
        actor: Optional[str] = None,
        action_types: Optional[List[str]] = None,
        target_type: Optional[str] = None,
        from_ts: Optional[Any] = None,
        to_ts: Optional[Any] = None,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        return []

    def dataset_source(self, dataset: str) -> str:
        return "ok"


def _build_client() -> TestClient:
    read_store = _AuditReadStore()
    service = IncidentService(get_read_store=lambda: read_store)
    router = create_incident_router(service=service, extract_identity=extract_identity_stub)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


CLIENT = _build_client()


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
