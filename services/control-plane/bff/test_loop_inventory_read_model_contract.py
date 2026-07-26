from __future__ import annotations

from copy import deepcopy
import sys
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient


BFF_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BFF_DIR))

import main as bff_main  # noqa: E402
import loop_inventory as loop_inventory_model  # noqa: E402
from services.runtime_auth_inbound import encode_jwt_hs256  # noqa: E402


HEADERS = {"Authorization": "Bearer loop-inventory-operator:operator,reviewer,admin:mfa"}


def _response_schema_ref(schema: dict[str, Any], path: str) -> str:
    response_schema = schema["paths"][path]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    if "$ref" in response_schema:
        return response_schema["$ref"].rsplit("/", 1)[-1]
    if response_schema.get("allOf"):
        return response_schema["allOf"][0]["$ref"].rsplit("/", 1)[-1]
    raise AssertionError(f"{path} does not publish a component response schema: {response_schema}")


def test_loop_read_models_publish_typed_openapi_envelopes() -> None:
    bff_main.app.openapi_schema = None
    schema = TestClient(bff_main.app).get("/openapi.json").json()
    expected = {
        "/bff/v5/loop-inventory": "LoopInventoryListEnvelope",
        "/bff/v5/loop-inventory/{loop_id}": "LoopInventoryDetailEnvelope",
        "/bff/v5/loop-health": "LoopHealthListEnvelope",
        "/bff/v5/loop-health/{loop_id}": "LoopHealthDetailEnvelope",
    }

    for path, component in expected.items():
        assert _response_schema_ref(schema, path) == component

    components = schema["components"]["schemas"]
    for component in (
        "LoopInventoryEntry",
        "LoopInventoryListEnvelope",
        "LoopInventoryDetailEnvelope",
        "LoopHealthEntry",
        "LoopHealthListEnvelope",
        "LoopHealthDetailEnvelope",
    ):
        assert component in components


def test_loop_read_models_enforce_strict_jwt_auth_and_read_roles(monkeypatch) -> None:
    secret = "loop-prod-strict-jwt-test-secret"
    issuer = "pantheon-loop-prod-test"
    audience = "bff-operators"
    now = int(time.time())

    def bearer(
        *roles: str,
        tenant_id: str | None = None,
    ) -> dict[str, str]:
        claims: dict[str, Any] = {
            "sub": f"loop-prod-{'-'.join(roles)}",
            "roles": list(roles),
            "iss": issuer,
            "aud": audience,
            "iat": now,
            "exp": now + 300,
        }
        if tenant_id is not None:
            claims["tenant_id"] = tenant_id
        token = encode_jwt_hs256(
            claims,
            secret=secret,
        )
        return {"Authorization": f"Bearer {token}"}

    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "false")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", secret)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", issuer)
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", audience)
    monkeypatch.setenv("PANTHEON_BFF_MFA_REQUIRED", "false")
    monkeypatch.delenv("PANTHEON_BFF_JWKS_URI", raising=False)
    monkeypatch.delenv("PANTHEON_BFF_OIDC_DISCOVERY_URL", raising=False)
    client = TestClient(bff_main.app, raise_server_exceptions=False)

    for path in ("/bff/v5/loop-inventory", "/bff/v5/loop-health"):
        assert client.get(path).status_code == 401
        assert client.get(path, headers=bearer("audit_only")).status_code == 403

    assert client.get(
        "/bff/v5/loop-inventory",
        headers=bearer("viewer"),
    ).status_code == 200
    unscoped = client.get(
        "/bff/v5/loop-health",
        headers=bearer("viewer"),
    )
    assert unscoped.status_code == 403
    assert unscoped.json()["error"]["details"]["precondition_failed"] == "tenant_scope"
    assert client.get(
        "/bff/v5/loop-health",
        headers=bearer("viewer", tenant_id="tenant-loop-prod"),
    ).status_code == 200


def test_loop_inventory_list_exposes_sa21_catalog_for_operator_surfaces(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    client = TestClient(bff_main.app, raise_server_exceptions=False)

    response = client.get("/bff/v5/loop-inventory", headers=HEADERS)

    assert response.status_code == 200, response.text
    payload = response.json()
    items = payload["items"]
    assert len(items) == 13
    assert payload["meta"]["catalog"]["inventory_counts"] == {
        "canonical_loop_count": 12,
        "composite_overlay_count": 1,
        "inventory_entry_count": 13,
    }
    assert payload["meta"]["catalog"]["continuous_resident_execution_loop_ids"] == [
        "capital_pool_execution"
    ]
    assert payload["meta"]["surfaces"]["loop_inventory"]["source"] == "bff_local_registry"
    assert payload["meta"]["surfaces"]["loop_inventory"]["truth_level"] == "registry_metadata"
    assert payload["meta"]["catalog"]["registry_ref"] == "docs/deployment/loop-catalog.registry.json"

    source_ingestion = next(item for item in items if item["loop_id"] == "source_ingestion")
    assert source_ingestion["current_maturity"] == "api-only"
    assert source_ingestion["target_maturity"] == "reconciled"
    assert source_ingestion["owner"]["authoritative_write_owner"]
    assert source_ingestion["evidence"]["registry_metadata"]["status"] == "present"
    assert source_ingestion["truth_source"]["level"] == "registry_metadata"
    assert source_ingestion["classification"] == "canonical"

    ooda_overlay = next(item for item in items if item["loop_id"] == "per_persona_ooda")
    assert ooda_overlay["classification"] == "composite_overlay"
    assert ooda_overlay["composed_of"]
    assert "capital_pool_execution" not in ooda_overlay["composed_of"]
    assert ooda_overlay["trigger_model"]["continuous"] is False
    assert ooda_overlay["maturity_projection"]["archived_task_completion_accepted"] is False


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
    assert payload["meta"]["catalog"]["catalog_id"] == "global-loop-catalog-2026-07-13"


def test_loop_inventory_detail_unknown_id_is_404(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    client = TestClient(bff_main.app, raise_server_exceptions=False)

    response = client.get("/bff/v5/loop-inventory/not-a-loop", headers=HEADERS)

    assert response.status_code == 404, response.text


def test_loop_inventory_archive_completion_and_catalog_claim_do_not_create_liveness(
    monkeypatch,
) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    registry = deepcopy(loop_inventory_model._load_registry())
    source_loop = registry["loops"][0]
    source_loop["maturity"]["current"] = "proven-live"
    source_loop["controller_contract"].update(
        {
            "status": "proven_live",
            "controller_name": "source-controller",
            "desired_state_query": "desired sources",
            "actual_state_query": "actual schedules",
            "restart_behavior": "resume from durable cursor",
            "liveness_metric": "last_reconcile_at",
        }
    )
    source_loop["evidence_profile"]["reconciled_live_proof"]["status"] = "present"
    source_loop["evidence_profile"]["proven_live_evidence"]["status"] = "present"
    for task_ref in source_loop["execution_tasks"]:
        task_ref["terminal_status"] = "done"
        task_ref["archive_ref"] = f"ai-task-archive/tasks/{task_ref['task_id']}.json"
    monkeypatch.setattr(loop_inventory_model, "_load_registry", lambda: registry)
    client = TestClient(bff_main.app, raise_server_exceptions=False)

    response = client.get("/bff/v5/loop-inventory/source_ingestion", headers=HEADERS)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["current_maturity"] == "proven-live"
    assert data["maturity_projection"]["task_completion_policy"] == "reference_only"
    assert data["live_status"]["catalog_claim_eligible"] is True
    assert data["live_status"]["has_live_evidence"] is False
    assert data["live_status"]["is_reconciled"] is False
    assert data["live_status"]["is_live"] is False
    assert data["live_status"]["reason"] == "catalog metadata is not live liveness proof"
