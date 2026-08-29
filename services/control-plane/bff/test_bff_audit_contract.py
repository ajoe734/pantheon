"""
Contract tests for P0-AUD-001: GET /bff/audit read endpoint.

Verifies: list envelope, filter params, pagination, RBAC, and read-surface meta.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from typing import Any

from ports import ReadSurfacePorts, create_in_memory_read_surface_ports

OPERATOR_HEADERS = {"Authorization": "Bearer op-aud:operator"}
ANON_HEADERS: dict = {}


class AuditTestReadPorts(ReadSurfacePorts):
    def __init__(self, data: dict | None = None, *, allow_fallback: bool = True) -> None:
        super().__init__()
        self._allow_fallback = allow_fallback
        if data is not None:
            self._data = data
        elif allow_fallback:
            self._data = {
                "governance_audit_events": {
                    "aud-001": {
                        "entry_id": "aud-001",
                        "auditId": "aud-001",
                        "id": "aud-001",
                        "actor": "fixture-governance-reviewer",
                        "target_type": "strategy",
                        "action_type": "route_policy_published",
                        "created_at": "2026-05-01T12:00:00Z",
                    },
                    "aud-002": {
                        "entry_id": "aud-002",
                        "auditId": "aud-002",
                        "id": "aud-002",
                        "actor": "fixture-governance-reviewer",
                        "target_type": "strategy",
                        "action_type": "route_policy_published",
                        "created_at": "2026-05-01T12:05:00Z",
                    },
                }
            }
        else:
            self._data = {}

    def list_governance_audit_events(
        self,
        *,
        actor: str | None = None,
        action_types: list[str] | None = None,
        target_type: str | None = None,
        from_ts: Any = None,
        to_ts: Any = None,
        include_fixture_pack: bool = True,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        events = list(self._data.get("governance_audit_events", {}).values())
        filtered = []
        for e in events:
            if actor and e.get("actor") != actor:
                continue
            if target_type and e.get("target_type") != target_type:
                continue
            if action_types and e.get("action_type") not in action_types:
                continue
            filtered.append(e)
        return filtered


def _fresh_client(td: str, *, allow_fallback: bool = True) -> TestClient:
    bff_main.read_store = AuditTestReadPorts(allow_fallback=allow_fallback)
    return TestClient(bff_main.app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Basic list contract
# ---------------------------------------------------------------------------

def test_bff_audit_returns_200_with_standard_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/audit", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body
            assert "items" in body
            assert "page_info" in body
            assert "meta" in body
            assert isinstance(body["data"], list)
            assert isinstance(body["items"], list)
        finally:
            bff_main.read_store = original


def test_bff_audit_returns_fixture_events_from_pack_a() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td, allow_fallback=True)
            resp = client.get("/bff/audit", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            # pack_a fixture ships two governance_audit_events
            assert len(body["data"]) >= 2
        finally:
            bff_main.read_store = original


def test_bff_audit_page_info_has_total() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td, allow_fallback=True)
            resp = client.get("/bff/audit", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            page_info = resp.json()["page_info"]
            assert "total" in page_info
            assert isinstance(page_info["total"], int)
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# Filter params
# ---------------------------------------------------------------------------

def test_bff_audit_filter_by_actor() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td, allow_fallback=True)
            resp = client.get(
                "/bff/audit",
                params={"actor": "fixture-governance-reviewer"},
                headers=OPERATOR_HEADERS,
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            for event in body["data"]:
                assert event.get("actor") == "fixture-governance-reviewer"
        finally:
            bff_main.read_store = original


def test_bff_audit_filter_by_target_type() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td, allow_fallback=True)
            resp = client.get(
                "/bff/audit",
                params={"target_type": "strategy"},
                headers=OPERATOR_HEADERS,
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            for event in body["data"]:
                assert event.get("target_type") == "strategy"
        finally:
            bff_main.read_store = original


def test_bff_audit_filter_by_action_type() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td, allow_fallback=True)
            resp = client.get(
                "/bff/audit",
                params={"action_type": "route_policy_published"},
                headers=OPERATOR_HEADERS,
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            for event in body["data"]:
                assert event.get("action_type") == "route_policy_published"
        finally:
            bff_main.read_store = original


def test_bff_audit_filter_nonexistent_actor_returns_empty() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td, allow_fallback=True)
            resp = client.get(
                "/bff/audit",
                params={"actor": "no-such-actor-xyz"},
                headers=OPERATOR_HEADERS,
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["data"] == []
            assert body["page_info"]["total"] == 0
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

def test_bff_audit_pagination_respects_page_size() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td, allow_fallback=True)
            resp = client.get(
                "/bff/audit",
                params={"page_size": 1},
                headers=OPERATOR_HEADERS,
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert len(body["data"]) <= 1
            total = body["page_info"]["total"]
            if total > 1:
                assert body["page_info"]["next_page_token"] is not None
        finally:
            bff_main.read_store = original


def test_bff_audit_pagination_page_token_advances() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td, allow_fallback=True)
            first = client.get(
                "/bff/audit",
                params={"page_size": 1},
                headers=OPERATOR_HEADERS,
            )
            assert first.status_code == 200
            first_body = first.json()
            if first_body["page_info"]["next_page_token"] is None:
                pytest.skip("Only one event in fixture; pagination not exercised")
            token = first_body["page_info"]["next_page_token"]
            second = client.get(
                "/bff/audit",
                params={"page_size": 1, "page_token": token},
                headers=OPERATOR_HEADERS,
            )
            assert second.status_code == 200
            second_body = second.json()
            first_ids = {e.get("entry_id") for e in first_body["data"]}
            second_ids = {e.get("entry_id") for e in second_body["data"]}
            assert first_ids.isdisjoint(second_ids), "Pages must not overlap"
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------

def test_bff_audit_requires_auth() -> None:
    client = TestClient(bff_main.app, raise_server_exceptions=False)
    resp = client.get("/bff/audit", headers=ANON_HEADERS)
    assert resp.status_code in {401, 403}, resp.text


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------

def test_bff_audit_meta_has_snapshot_at() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/audit", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            meta = resp.json()["meta"]
            assert "snapshot_at" in meta
        finally:
            bff_main.read_store = original
