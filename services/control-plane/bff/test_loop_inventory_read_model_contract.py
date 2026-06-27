from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient


BFF_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BFF_DIR))

import main as bff_main  # noqa: E402


HEADERS = {"Authorization": "Bearer loop-inventory-operator:operator,reviewer,admin:mfa"}


def test_loop_inventory_list_exposes_sa21_catalog_for_operator_surfaces(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    client = TestClient(bff_main.app, raise_server_exceptions=False)

    response = client.get("/bff/v5/loop-inventory", headers=HEADERS)

    assert response.status_code == 200, response.text
    payload = response.json()
    items = payload["items"]
    assert len(items) == 12
    assert payload["meta"]["surfaces"]["loop_inventory"]["source"] == "bff_local_registry"
    assert payload["meta"]["surfaces"]["loop_inventory"]["truth_level"] == "registry_metadata"
    assert payload["meta"]["catalog"]["registry_ref"] == "docs/deployment/loop-catalog.registry.json"

    source_ingestion = next(item for item in items if item["loop_id"] == "source_ingestion")
    assert source_ingestion["current_maturity"] == "api-only"
    assert source_ingestion["target_maturity"] == "reconciled"
    assert source_ingestion["owner"]["authoritative_write_owner"]
    assert source_ingestion["evidence"]["registry_metadata"]["status"] == "present"
    assert source_ingestion["truth_source"]["level"] == "registry_metadata"


def test_loop_inventory_read_model_does_not_claim_live_without_present_live_evidence(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    client = TestClient(bff_main.app, raise_server_exceptions=False)

    response = client.get("/bff/v5/loop-inventory", headers=HEADERS)

    assert response.status_code == 200, response.text
    for item in response.json()["items"]:
        live_status = item["live_status"]
        assert live_status["is_live"] is False
        assert live_status["is_reconciled"] is False
        assert live_status["reason"] == "catalog metadata is not live liveness proof"

    capital_loop = next(item for item in response.json()["items"] if item["loop_id"] == "capital_pool_execution")
    assert capital_loop["evidence"]["proven_live_evidence"]["status"] == "historical"
    assert capital_loop["live_status"]["has_live_evidence"] is False


def test_loop_inventory_detail_returns_one_catalog_entry(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    client = TestClient(bff_main.app, raise_server_exceptions=False)

    response = client.get("/bff/v5/loop-inventory/promotion_deployment", headers=HEADERS)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["data"]["loop_id"] == "promotion_deployment"
    assert payload["data"]["current_maturity"] == "api-only"
    assert payload["data"]["target_maturity"] == "reconciled"
    assert payload["data"]["evidence_statuses"]["registry_metadata"] == "present"
    assert payload["meta"]["catalog"]["catalog_id"] == "global-loop-catalog-2026-06-27"


def test_loop_inventory_detail_unknown_id_is_404(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    client = TestClient(bff_main.app, raise_server_exceptions=False)

    response = client.get("/bff/v5/loop-inventory/not-a-loop", headers=HEADERS)

    assert response.status_code == 404, response.text
