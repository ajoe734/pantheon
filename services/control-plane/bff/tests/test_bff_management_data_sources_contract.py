"""Contract tests for GET /bff/management/data-sources.
- injects seeded read surface ports via isolated router fixture
- verifies envelope data shape, meta summary, meta surfaces, degradation
- verifies auth is required
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.control_plane.bff.console_gap.datasources import create_datasources_router
from services.control_plane.bff.models import OperatorIdentity
from services.control_plane.bff.ports import ReadSurfacePorts, create_in_memory_read_surface_ports
from services.control_plane.bff.source_management_client import SourceManagementClient


OPERATOR_HEADERS = {"Authorization": "Bearer op-ds-001:operator,reviewer"}

_SAMPLE_CONNECTORS = [
    {
        "connector_id": "conn-ibkr-equity",
        "provider": "IBKR",
        "kind": "market_data",
        "status": "active",
        "health": "ok",
        "universe": "US_equity",
        "last_heartbeat_at": "2026-06-15T08:00:00Z",
    },
    {
        "connector_id": "conn-kraken-crypto",
        "provider": "Kraken",
        "kind": "market_data",
        "status": "active",
        "health": "ok",
        "universe": "crypto_spot",
        "last_heartbeat_at": "2026-06-15T08:01:00Z",
    },
]


def _extract_identity(authorization: str | None) -> OperatorIdentity:
    if not authorization:
        raise HTTPException(status_code=401, detail={"error": "authentication_required"})
    token = str(authorization)
    roles: list[str] = []
    if "operator" in token:
        roles.append("operator")
    if "reviewer" in token:
        roles.append("reviewer")
    return OperatorIdentity(
        operator_id="op-ds-001",
        roles=roles,
        claims={},
    )


def _require_read_role(identity: OperatorIdentity) -> None:
    if not identity.roles:
        raise HTTPException(status_code=403, detail={"error": "forbidden"})


def _snapshot_meta(snapshot_at: str) -> dict[str, Any]:
    return {"snapshot_at": snapshot_at}


def _make_app(store: ReadSurfacePorts) -> FastAPI:
    app = FastAPI(title="Datasources router contract")
    router = create_datasources_router(
        read_surface=store,
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
        snapshot_meta=_snapshot_meta,
        utc_now=lambda: "2026-06-15T08:00:00Z",
        get_source_management_client=lambda: SourceManagementClient(base_url=""),
    )
    app.include_router(router)
    return app


def _client_with_connectors() -> TestClient:
    store = create_in_memory_read_surface_ports()
    store.get_source_connector_registry = lambda: {
        "source": "service_client",
        "connectors": _SAMPLE_CONNECTORS,
        "provider_examples": ["IBKR", "Kraken"],
        "policy_registry": {"default_universe": "US_equity"},
        "financial_data_source_catalog": None,
        "active_universe_policy": None,
    }
    return TestClient(_make_app(store))


def _client_source_missing() -> TestClient:
    store = create_in_memory_read_surface_ports()
    store.get_source_connector_registry = lambda: {
        "source": "missing",
        "connectors": [],
        "provider_examples": [],
        "policy_registry": None,
        "financial_data_source_catalog": None,
        "active_universe_policy": None,
    }
    return TestClient(_make_app(store))


def _client_source_unavailable() -> TestClient:
    store = create_in_memory_read_surface_ports()
    store.get_source_connector_registry = lambda: {
        "source": "unavailable",
        "connectors": [],
        "provider_examples": [],
        "policy_registry": None,
        "financial_data_source_catalog": None,
        "active_universe_policy": None,
    }
    return TestClient(_make_app(store))


# ---------------------------------------------------------------------------
# Happy path: service_client with connectors
# ---------------------------------------------------------------------------


def test_bff_management_data_sources_returns_canonical_envelope() -> None:
    client = _client_with_connectors()
    response = client.get(
        "/bff/management/data-sources", headers=OPERATOR_HEADERS
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    # canonical envelope keys
    assert "data" in payload
    assert "items" not in payload
    assert "page_info" in payload
    assert "meta" in payload

    # data object
    data = payload["data"]
    assert data["id"] == "management-data-sources"
    assert isinstance(data["items"], list)
    assert len(data["items"]) == 2
    assert data["status"] == "ok"
    assert data["source"] == "service_client"
    assert data["summary"]["total_items"] == 2
    assert data["summary"]["returned_items"] == 2

    # page_info
    pi = payload["page_info"]
    assert pi["next_page_token"] is None
    assert pi["total"] == 2
    assert pi["page_size"] == 50
    assert pi["returned"] == 2
    assert pi["has_more"] is False

    # meta
    meta = payload["meta"]
    assert meta["status"] == "ok"
    assert meta["source"] == "service_client"
    assert "snapshot_at" in meta
    assert meta["surfaces"]["data_sources"]["status"] == "ok"
    assert meta["surfaces"]["data_sources"]["source"] == "service_client"


def test_bff_management_data_sources_includes_connector_fields() -> None:
    client = _client_with_connectors()
    response = client.get(
        "/bff/management/data-sources", headers=OPERATOR_HEADERS
    )
    assert response.status_code == 200, response.text
    items = response.json()["data"]["items"]
    assert items[0]["connector_id"] == "conn-ibkr-equity"
    assert items[1]["provider"] == "Kraken"


# ---------------------------------------------------------------------------
# Degraded path: source missing (service unconfigured in dev)
# ---------------------------------------------------------------------------


def test_bff_management_data_sources_degraded_when_source_missing() -> None:
    client = _client_source_missing()
    response = client.get(
        "/bff/management/data-sources", headers=OPERATOR_HEADERS
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    # must NOT be a bare [] — must be a proper degraded envelope
    assert "items" not in payload
    assert payload["page_info"]["total"] == 0
    assert payload["page_info"]["returned"] == 0

    data = payload["data"]
    assert data["status"] == "unavailable"
    assert data["source"] == "missing"
    assert data["items"] == []

    meta = payload["meta"]
    assert meta["status"] == "unavailable"
    assert meta["source"] == "missing"
    assert meta["surfaces"]["data_sources"]["status"] == "unavailable"
    assert meta["surfaces"]["data_sources"]["source"] == "missing"
    assert meta["degradation"]["reason"]


def test_bff_management_data_sources_degraded_when_source_unavailable() -> None:
    client = _client_source_unavailable()
    response = client.get(
        "/bff/management/data-sources", headers=OPERATOR_HEADERS
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert "items" not in payload
    assert payload["data"]["items"] == []
    meta = payload["meta"]
    assert meta["status"] == "unavailable"
    assert meta["source"] == "unavailable"
    assert meta["surfaces"]["data_sources"]["status"] == "unavailable"
    assert meta["surfaces"]["data_sources"]["source"] == "unavailable"


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------


def test_bff_management_data_sources_requires_auth() -> None:
    store = create_in_memory_read_surface_ports()
    client = TestClient(_make_app(store), raise_server_exceptions=False)
    response = client.get("/bff/management/data-sources")
    assert response.status_code == 401
