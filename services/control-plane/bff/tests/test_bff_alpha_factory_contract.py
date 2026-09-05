"""Contract tests for GET /bff/alpha-factory (BFF-AF-001).

Covers:
  - Happy path: seeded store → 200 with canonical list envelope.
  - Empty / missing store → 200 with degraded envelope (status:unavailable, source:missing).
  - Auth guard: unauthenticated → 401.
  - Lane filter is reflected in response meta.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.control_plane.bff.console_gap.alpha_factory import create_alpha_factory_router
from services.control_plane.bff.models import OperatorIdentity
from services.control_plane.bff.ports import create_in_memory_read_surface_ports

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


def _make_client(store: Any) -> TestClient:
    def _extract_identity(authorization: str | None) -> OperatorIdentity:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Authentication required")
        raw = authorization[len("Bearer "):].strip()
        parts = raw.split(":")
        operator_id = parts[0] if parts else "op"
        roles = parts[1].split(",") if len(parts) > 1 else []
        return OperatorIdentity(operator_id=operator_id, roles=roles, claims={})

    def _require_read_role(identity: OperatorIdentity) -> None:
        if not identity or not identity.roles:
            raise HTTPException(status_code=403, detail="Forbidden")

    app = FastAPI(title="Alpha Factory Contract")
    router = create_alpha_factory_router(
        read_surface=store,
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
        utc_now=lambda: "2026-06-15T08:00:00Z",
    )
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def _seeded_client(td: str = "") -> TestClient:
    store = create_in_memory_read_surface_ports()
    store.dataset_source = lambda dataset, **kwargs: (
        "service_store" if dataset == "alpha_factory_cards" else "missing"
    )
    store.list_alpha_factory_cards = lambda page=1, page_size=20, lane=None: (
        [c for c in _SAMPLE_CARDS if lane is None or c["lane"] == lane]
    )
    return _make_client(store)


def _missing_client(td: str = "") -> TestClient:
    store = create_in_memory_read_surface_ports()
    return _make_client(store)


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


def test_alpha_factory_returns_canonical_list_envelope() -> None:
    client = _seeded_client()
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


def test_alpha_factory_lane_filter_is_honoured() -> None:
    client = _seeded_client()
    r = client.get("/bff/alpha-factory?lane=ideas", headers=OPERATOR_HEADERS)
    assert r.status_code == 200, r.text
    payload = r.json()
    assert all(c["lane"] == "ideas" for c in payload["items"])
    assert payload["meta"]["filters"]["lane"] == "ideas"


# ---------------------------------------------------------------------------
# Degraded / missing store tests
# ---------------------------------------------------------------------------


def test_alpha_factory_missing_store_returns_degraded_envelope() -> None:
    """When dataset_source returns 'missing', items must be [] and surface unavailable."""
    client = _missing_client()
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


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------


def test_alpha_factory_requires_auth() -> None:
    client = _seeded_client()
    r = client.get("/bff/alpha-factory")
    assert r.status_code == 401

