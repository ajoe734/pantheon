"""Regression: audit time-range filters must not 500 on any from_ts/to_ts value.

Verification campaign 2026-06-14, round 16, finding F12. The audit events/export
handlers called `_parse_rfc3339_header`, which was defined nowhere — a NameError
on every non-empty from_ts/to_ts (even a valid timestamp), surfacing as 500. The
same missing-symbol affected `_parse_rfc3339` call sites
(`_kw04_within_recency`, aggregated-recency). Fixed by defining
`_parse_rfc3339` in main and repointing the calls.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

BFF_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BFF_DIR))

os.environ.setdefault("PANTHEON_BFF_AUTH_STUB", "true")
os.environ.setdefault("PANTHEON_BFF_AUTH_MODE", "permissive")

from services.control_plane.bff import main as bff_main
from fastapi.testclient import TestClient  # noqa: E402

CLIENT = TestClient(bff_main.app)
HEADERS = {"Authorization": "Bearer op-audit:operator,admin,reviewer:mfa"}


def test_parse_rfc3339_is_defined_and_safe():
    assert hasattr(bff_main, "_parse_rfc3339")
    assert bff_main._parse_rfc3339("2026-01-01T00:00:00Z") is not None
    assert bff_main._parse_rfc3339("-1") is None
    assert bff_main._parse_rfc3339("") is None
    assert bff_main._parse_rfc3339(None) is None


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
