"""Contract tests for GET /bff/management/data-sources.
- injects seeded read surface ports directly into the mounted router
- verifies envelope data shape, meta summary, meta surfaces, degradation
- verifies auth is required
"""
from __future__ import annotations

import importlib
import sys
import unittest.mock
from typing import Any

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.core.errors import register_error_handlers
from services.control_plane.bff.console_gap.datasources import create_datasources_router
from services.control_plane.bff.management_read_models.router import (
    _default_bff_error,
    _default_extract_identity,
    _default_require_read_role,
    _default_snapshot_meta,
    _utc_now_rfc3339,
)
from services.control_plane.bff.ports import create_in_memory_read_surface_ports


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


def _mounted_client(store: Any) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(
        create_datasources_router(
            read_surface=store,
            extract_identity=_default_extract_identity,
            require_read_role=_default_require_read_role,
            snapshot_meta=_default_snapshot_meta,
            utc_now=_utc_now_rfc3339,
            bff_error=_default_bff_error,
        )
    )
    return TestClient(app, raise_server_exceptions=False)


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
    return _mounted_client(store)


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
    return _mounted_client(store)


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
    return _mounted_client(store)


# ---------------------------------------------------------------------------
# Happy path: service_client with connectors
# ---------------------------------------------------------------------------


def test_bff_management_data_sources_returns_canonical_envelope() -> None:
    client = _client_with_connectors()
    response = client.get("/bff/management/data-sources", headers=OPERATOR_HEADERS)
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
    response = client.get("/bff/management/data-sources", headers=OPERATOR_HEADERS)
    assert response.status_code == 200, response.text
    items = response.json()["data"]["items"]
    assert items[0]["connector_id"] == "conn-ibkr-equity"
    assert items[1]["provider"] == "Kraken"


# ---------------------------------------------------------------------------
# Degraded path: source missing (service unconfigured in dev)
# ---------------------------------------------------------------------------


def test_bff_management_data_sources_degraded_when_source_missing() -> None:
    client = _client_source_missing()
    response = client.get("/bff/management/data-sources", headers=OPERATOR_HEADERS)
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
    response = client.get("/bff/management/data-sources", headers=OPERATOR_HEADERS)
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
    client = _client_with_connectors()
    response = client.get("/bff/management/data-sources")
    assert response.status_code == 401


def test_calculate_source_allowed_actions_fails_closed_when_import_unavailable() -> None:
    """Re-import the real module with the policy unavailable; a permissive fallback in it fails this test."""
    module_name = "services.control_plane.bff.console_gap.datasources"
    unavailable = {"services.source_ingestion.connector_definitions": None, "connector_definitions": None}
    with unittest.mock.patch.dict("sys.modules", unavailable):  # patch.dict restores sys.modules, including the real module
        sys.modules.pop(module_name, None)
        module = importlib.import_module(module_name)
        with pytest.raises(RuntimeError, match="Source action policy is unavailable"):
            module.calculate_source_allowed_actions({}, {}, {}, {})


# ---------------------------------------------------------------------------
# Empty v2 store fallback to explicit degraded legacy_projection (7 real connectors)
# ---------------------------------------------------------------------------

_REAL_REGISTRY_CONNECTOR_KEYS = {
    "connector_id",
    "crawler_policy",
    "fetch_policy",
    "freshness",
    "health_metrics",
    "metadata",
    "policy",
    "provider",
    "schedule",
    "schema_version",
    "source_type",
    "state",
    "status",
    "supported_modes",
}


def _real_registry_connector(connector_id: str, provider: str, status: str) -> dict[str, Any]:
    return {
        "schema_version": "source_connector_registry_entry.v1",
        "connector_id": connector_id,
        "provider": provider,
        "source_type": "market_data",
        "status": status,
        "supported_modes": ["scheduled", "on_demand"],
        "policy": {"license_scope": "official_reference", "allowed_use": ["research_data"]},
        "metadata": {"market": "TW" if connector_id.startswith("tw-") else "US"},
        "fetch_policy": {"mode": "external_feed", "max_records": 1000, "timeout_seconds": 30},
        "schedule": {"connector_id": connector_id, "enabled": status == "enabled", "interval_seconds": 3600},
        "freshness": {
            "connector_id": connector_id,
            "status": "fresh" if status == "enabled" else "idle",
            "last_success_at": "2026-11-10T08:00:00Z" if status == "enabled" else None,
            "last_watermark": "2026-11-10" if status == "enabled" else None,
            "staleness_seconds": 60 if status == "enabled" else None,
        },
        "health_metrics": {
            "schema_version": "source_connector_health_metrics.v1",
            "last_success_at": "2026-11-10T08:00:00Z" if status == "enabled" else None,
            "row_count": 100 if status == "enabled" else None,
            "expected_rows": 100,
            "watermark": "2026-11-10" if status == "enabled" else None,
            "schema_hash": "0123456789abcdef",
            "staleness_seconds": 60 if status == "enabled" else None,
            "source_error": None,
        },
        "state": {
            "connector_id": connector_id,
            "attempts": 1 if status == "enabled" else 0,
            "successful_attempts": 1 if status == "enabled" else 0,
            "failed_attempts": 0,
            "last_error": None,
            "updated_at": "2026-11-10T08:00:00Z" if status == "enabled" else None,
        },
        "crawler_policy": {"connector_id": connector_id, "rate_limit_per_minute": 30},
    }


_REAL_SEVEN_CONNECTORS = [
    _real_registry_connector("tw-twse-tpex-official-market", "TWSE/TPEx", "enabled"),
    _real_registry_connector("tw-taifex-futures-options-chip", "TAIFEX", "disabled"),
    _real_registry_connector("dev-paper-us-equity-simulation", "Explicit controlled simulation", "disabled"),
    _real_registry_connector(
        "conn-dev-product-20260913T040439Z-7935481ca72f49629606e01098c3a54e",
        "Explicit controlled simulation",
        "enabled",
    ),
    _real_registry_connector("hosted-all12-h12-01a11-20261023-9aeb-conn", "L12 deployed E2E controlled source", "enabled"),
    _real_registry_connector("hosted-all12-h12-01a11-20261026-d834-conn", "L12 deployed E2E controlled source", "enabled"),
    _real_registry_connector("hosted-all12-h12-claude-20261027-4923-conn", "L12 deployed E2E controlled source", "enabled"),
]


def test_bff_management_data_sources_empty_v2_store_returns_degraded_legacy_projection() -> None:
    from services.control_plane.bff.source_management_client import SourceManagementClient

    class _EmptyV2Client(SourceManagementClient):
        def __init__(self) -> None:
            super().__init__(base_url="http://source-ingest:8000", service_token="svc-token")

        def list_sources(self, *, source_kind: Any = None, lifecycle_state: Any = None) -> dict[str, Any]:
            return {"sources": [], "count": 0}

        def list_connector_definitions(self) -> dict[str, Any]:
            return {"definitions": [], "count": 0}

    store = create_in_memory_read_surface_ports()
    store.get_source_connector_registry = lambda: {
        "source": "service_client",
        "schema_version": "source_connector_registry.v1",
        "connectors": list(_REAL_SEVEN_CONNECTORS),
        "provider_examples": [],
        "policy_registry": {"max_records_per_job": 1000},
        "financial_data_source_catalog": None,
        "active_universe_policy": None,
    }

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(
        create_datasources_router(
            read_surface=store,
            extract_identity=_default_extract_identity,
            require_read_role=_default_require_read_role,
            snapshot_meta=_default_snapshot_meta,
            utc_now=_utc_now_rfc3339,
            get_source_management_client=lambda: _EmptyV2Client(),
            bff_error=_default_bff_error,
        )
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/bff/management/data-sources", headers=OPERATOR_HEADERS)
    assert response.status_code == 200, response.text
    payload = response.json()

    meta = payload["meta"]
    assert meta["status"] == "degraded"
    assert meta["source"] == "legacy_projection"
    assert "empty" in meta["degradation"]["reason"].lower()
    assert meta["surfaces"]["data_sources"]["status"] == "degraded"
    assert meta["surfaces"]["data_sources"]["source"] == "legacy_projection"
    assert "empty" in meta["surfaces"]["data_sources"]["reason"].lower()

    data = payload["data"]
    assert data["status"] == "degraded"
    assert data["source"] == "legacy_projection"
    assert data["summary"]["status"] == "degraded"
    assert data["summary"]["source"] == "legacy_projection"
    assert data["summary"]["total_items"] == 7
    assert data["summary"]["returned_items"] == 7
    assert payload["page_info"]["total"] == 7
    assert payload["page_info"]["returned"] == 7

    items = data["items"]
    assert len(items) == 7
    for item in items:
        assert set(item.keys()) == _REAL_REGISTRY_CONNECTOR_KEYS
        assert "allowed_actions" not in item
        assert "allowedActions" not in item
        assert "persona_fleet_status" not in item
        assert "inferred_persona_fleet_status" not in item

