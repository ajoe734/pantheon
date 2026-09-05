"""Contract tests for GET /bff/alpha-factory (BFF-AF-001).

Covers:
  - Happy path: seeded store → 200 with canonical list envelope.
  - Empty / missing store → 200 with degraded envelope (status:unavailable, source:missing).
  - Auth guard: unauthenticated → 401.
  - Lane filter is reflected in response meta.
"""
from __future__ import annotations

import os
import sys
import tempfile

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from services.control_plane.bff import main as bff_main
from ports import create_in_memory_read_surface_ports

OPERATOR_HEADERS = {"Authorization": "Bearer op-af:operator,reviewer"}

_SAMPLE_CARDS = [
    {
        "id": "card-af-001",
        "lane": "ideas",
        "title": "Mean-reversion on AAPL intraday",
        "status": "draft",
        "created_at": "2026-06-10T09:00:00Z",
    },
    {
        "id": "card-af-002",
        "lane": "strategies",
        "title": "Momentum cross-section v2",
        "status": "active",
        "created_at": "2026-06-10T10:00:00Z",
    },
]


def _seeded_client(td: str) -> TestClient:
    store = create_in_memory_read_surface_ports()
    store.dataset_source = lambda dataset, **kwargs: (
        "service_store" if dataset == "alpha_factory_cards" else "missing"
    )
    store.list_alpha_factory_cards = lambda page=1, page_size=20, lane=None: (
        [c for c in _SAMPLE_CARDS if lane is None or c["lane"] == lane]
    )
    bff_main.read_store = store
    return TestClient(bff_main.app)


def _missing_client(td: str) -> TestClient:
    store = create_in_memory_read_surface_ports()
    # dataset_source returns "missing" for everything (default behaviour)
    bff_main.read_store = store
    return TestClient(bff_main.app)


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


def test_alpha_factory_returns_canonical_list_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _seeded_client(td)
            r = client.get("/bff/alpha-factory", headers=OPERATOR_HEADERS)
            assert r.status_code == 200, r.text
            payload = r.json()

            # top-level canonical envelope keys
            assert "data" in payload
            assert "items" in payload
            assert "page_info" in payload
            assert "meta" in payload

            # data shape
            data = payload["data"]
            assert data["id"] == "alpha-factory"
            assert "snapshotAt" in data or "snapshot_at" in data
            assert isinstance(data["lanes"], list)
            assert len(data["lanes"]) == 3
            assert isinstance(data["items"], list)

            # items match seeded cards
            assert len(payload["items"]) == 2
            ids = {c["id"] for c in payload["items"]}
            assert "card-af-001" in ids
            assert "card-af-002" in ids

            # page_info
            pi = payload["page_info"]
            assert pi["total"] == 2
            assert pi["page"] == 1
            assert pi["page_size"] == 20

            # meta surface ok
            surface = payload["meta"]["surfaces"]["alpha_factory"]
            assert surface["status"] == "ok"
            assert surface["source"] == "service_store"
        finally:
            bff_main.read_store = original


def test_alpha_factory_lane_filter_is_honoured() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _seeded_client(td)
            r = client.get("/bff/alpha-factory?lane=ideas", headers=OPERATOR_HEADERS)
            assert r.status_code == 200, r.text
            payload = r.json()
            assert all(c["lane"] == "ideas" for c in payload["items"])
            assert payload["meta"]["filters"]["lane"] == "ideas"
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# Degraded / missing store tests
# ---------------------------------------------------------------------------


def test_alpha_factory_missing_store_returns_degraded_envelope() -> None:
    """When dataset_source returns 'missing', items must be [] and surface unavailable."""
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _missing_client(td)
            r = client.get("/bff/alpha-factory", headers=OPERATOR_HEADERS)
            assert r.status_code == 200, r.text
            payload = r.json()

            # items must be empty list, not omitted
            assert payload["items"] == []
            assert payload["data"]["items"] == []

            # surface status must be unavailable with source:missing
            surface = payload["meta"]["surfaces"]["alpha_factory"]
            assert surface["status"] == "unavailable"
            assert surface["source"] == "missing"

            # page_info still present
            assert "page_info" in payload
            assert payload["page_info"]["total"] == 0
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------


def test_alpha_factory_requires_auth() -> None:
    client = TestClient(bff_main.app, raise_server_exceptions=False)
    r = client.get("/bff/alpha-factory")
    assert r.status_code == 401
