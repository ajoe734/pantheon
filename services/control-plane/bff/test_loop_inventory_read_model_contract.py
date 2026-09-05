from __future__ import annotations

from copy import deepcopy
import importlib.util
import re
import sys
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient


BFF_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BFF_DIR))

from services.control_plane.bff import main as bff_main
import loop_inventory as loop_inventory_model  # noqa: E402
from services.runtime_auth_inbound import encode_jwt_hs256  # noqa: E402


HEADERS = {"Authorization": "Bearer loop-inventory-operator:operator,reviewer,admin:mfa"}

REPO_ROOT = BFF_DIR.parents[2]
COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"

# L12-TRUTH-001 catalog-to-runtime exactness binding.
#
# The catalog names a controller; this table names the code and the deployed
# Compose service that must actually produce it.  Both directions are checked:
# a catalog claim without a deployed controller fails, and a deployed
# controller missing from the catalog fails.  ``module_literals`` are the
# strings the controller module must contain for the catalog's declared
# queries and restart behavior to be a description of real code rather than a
# plan.
RUNTIME_CONTROLLER_BINDINGS: dict[str, dict[str, Any]] = {
    "source_ingestion": {
        "controller_name": "source-ingestion-controller",
        "module": "services/source_ingestion/controller_worker.py",
        "compose_services": ("source-ingest-scheduler",),
        "module_literals": (
            "persona/data requirement snapshot",
            "/api/source-ingest/controller/readback",
            "SOURCE_INGEST_CONTROLLER_STATE_PATH",
            "refresh_runtime_identity",
        ),
    },
    "strategy_distillation": {
        "controller_name": "strategy-distillation-controller",
        "module": "services/source_ingestion/distillation_controller.py",
        "compose_services": ("strategy-distillation-worker",),
        "module_literals": (
            "list_source_records",
            "DistillationJobQueue",
            "DISTILLATION_CONTROLLER_STATE_PATH",
            "refresh_runtime_identity",
        ),
    },
    "alpha_replication": {
        "controller_name": "alpha-replication-controller",
        "module": "services/research/alpha_replication/replication_controller.py",
        "compose_services": ("alpha-replication-worker",),
        "module_literals": (
            "AlphaReplicationQueue",
            "AlphaRevalidationWorker",
            "ALPHA_REPLICATION_CONTROLLER_STATE_PATH",
        ),
    },
}

CONTROLLER_CONTRACT_REQUIRED_FIELDS = (
    "controller_name",
    "desired_state_query",
    "actual_state_query",
    "restart_behavior",
    "liveness_metric",
)

# Until hosted runtime evidence is admitted (L12-HOSTED-001) the catalog may
# say a controller is implemented but must not say a loop is reconciled or
# proven-live.
MATURITY_CEILING_FORBIDDEN = {"reconciled", "proven-live"}
CONTROLLER_STATUS_CEILING_FORBIDDEN = {"proven_live"}
LIVE_EVIDENCE_LEVELS = ("reconciled_live_proof", "proven_live_evidence")


def _loop_conformance_module():
    """Import the loop-control conformance contract without its asyncpg deps."""

    path = REPO_ROOT / "services" / "loop-control" / "conformance.py"
    spec = importlib.util.spec_from_file_location("l12_loop_conformance", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _compose_controller_services() -> dict[str, str]:
    """Map every Compose service that declares a controller name to that name."""

    services: dict[str, str] = {}
    current: str | None = None
    for line in COMPOSE_PATH.read_text(encoding="utf-8").splitlines():
        service_match = re.match(r"^  ([A-Za-z0-9_.-]+):\s*$", line)
        if service_match:
            current = service_match.group(1)
            continue
        name_match = re.match(
            r"^\s*PANTHEON_CONTROLLER_NAME:\s*(\S+)\s*$",
            line,
        )
        if name_match and current:
            services[current] = name_match.group(1).strip("\"'")
    return services


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
    assert payload["meta"]["catalog"]["runtime_projection_policy"]["catalog_role"] == (
        "stable_loop_spec_and_owner_contract_only"
    )

    source_ingestion = next(item for item in items if item["loop_id"] == "source_ingestion")
    assert source_ingestion["owner"]["authoritative_write_owner"]
    assert source_ingestion["truth_source"]["level"] == "registry_metadata"
    assert source_ingestion["classification"] == "canonical"
    for retired_runtime_field in (
        "current_maturity",
        "target_maturity",
        "maturity",
        "evidence",
        "evidence_statuses",
        "execution_tasks",
        "maturity_projection",
    ):
        assert retired_runtime_field not in source_ingestion

    ooda_overlay = next(item for item in items if item["loop_id"] == "per_persona_ooda")
    assert ooda_overlay["classification"] == "composite_overlay"
    assert ooda_overlay["composed_of"]
    assert "capital_pool_execution" not in ooda_overlay["composed_of"]
    assert ooda_overlay["trigger_model"]["continuous"] is False
    assert "maturity_projection" not in ooda_overlay


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
    assert "evidence" not in capital_loop
    assert capital_loop["live_status"]["has_live_evidence"] is False


def test_loop_inventory_detail_returns_one_catalog_entry(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    client = TestClient(bff_main.app, raise_server_exceptions=False)

    response = client.get("/bff/v5/loop-inventory/promotion_deployment", headers=HEADERS)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["data"]["loop_id"] == "promotion_deployment"
    assert "current_maturity" not in payload["data"]
    assert "target_maturity" not in payload["data"]
    assert "evidence_statuses" not in payload["data"]
    assert payload["meta"]["catalog"]["catalog_id"] == "global-loop-catalog-2026-07-13"


def test_loop_inventory_detail_unknown_id_is_404(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    client = TestClient(bff_main.app, raise_server_exceptions=False)

    response = client.get("/bff/v5/loop-inventory/not-a-loop", headers=HEADERS)

    assert response.status_code == 404, response.text


def test_loop_catalog_ids_match_the_canonical_controller_record_contract() -> None:
    """The catalog and the controller-record contract must name the same loops."""

    conformance = _loop_conformance_module()
    registry = loop_inventory_model._load_registry()
    catalog_ids = [loop["loop_id"] for loop in registry["loops"]]

    assert len(catalog_ids) == 12
    assert catalog_ids == list(conformance.CANONICAL_LOOP_IDS)
    assert [
        overlay["loop_id"] for overlay in registry["composite_overlays"]
    ] == ["per_persona_ooda"]
    # Overlays compose canonical loops; they must never claim a canonical id.
    assert not set(conformance.CANONICAL_LOOP_IDS) & {
        overlay["loop_id"] for overlay in registry["composite_overlays"]
    }


def test_loop_catalog_controller_contract_matches_runtime_implementation() -> None:
    """Catalog names, queries, restart, and liveness must describe real code.

    This is the catalog-to-runtime exactness check.  It fails both when the
    catalog claims a controller that is not deployed and when a controller is
    deployed without the catalog admitting it.
    """

    conformance = _loop_conformance_module()
    registry = loop_inventory_model._load_registry()
    compose_services = _compose_controller_services()

    catalog_implemented: dict[str, str] = {}
    expected_compose: dict[str, str] = {}

    for loop in registry["loops"] + registry["composite_overlays"]:
        loop_id = loop["loop_id"]
        contract = loop["controller_contract"]
        status = contract["status"]

        if status not in {"implemented", "proven_live"}:
            assert status == "not_implemented", (loop_id, status)
            for field in CONTROLLER_CONTRACT_REQUIRED_FIELDS:
                assert contract[field] is None, (
                    f"{loop_id} declares {field} without an implemented controller"
                )
            assert loop_id not in RUNTIME_CONTROLLER_BINDINGS, (
                f"{loop_id} has a runtime controller binding but the catalog "
                "still says not_implemented"
            )
            assert loop["owner"]["current_controller_owner"] is None, loop_id
            continue

        for field in CONTROLLER_CONTRACT_REQUIRED_FIELDS:
            assert str(contract[field] or "").strip(), (
                f"{loop_id} declares {status} without {field}"
            )

        binding = RUNTIME_CONTROLLER_BINDINGS.get(loop_id)
        assert binding is not None, (
            f"{loop_id} claims an implemented controller with no runtime binding"
        )
        controller_name = contract["controller_name"]
        assert controller_name == binding["controller_name"], loop_id
        catalog_implemented[loop_id] = controller_name

        # The catalog's controller owner must agree with the contract.
        assert loop["owner"]["current_controller_owner"] == controller_name, loop_id

        # The declared liveness metric must be a real controller record field.
        assert contract["liveness_metric"] in conformance.CONTROLLER_RECORD_FIELDS, (
            f"{loop_id} liveness_metric is not a controller record field"
        )

        module_path = REPO_ROOT / binding["module"]
        assert module_path.is_file(), f"{loop_id}: missing {binding['module']}"
        source = module_path.read_text(encoding="utf-8")
        assert controller_name in source, (
            f"{loop_id}: {binding['module']} does not default to {controller_name}"
        )
        assert contract["desired_state_query"], loop_id
        for literal in binding["module_literals"]:
            assert literal in source, (
                f"{loop_id}: {binding['module']} does not contain {literal!r}"
            )
        # The declared actual-state query must be traceable to the module.
        assert any(
            fragment in source
            for fragment in (contract["actual_state_query"], *binding["module_literals"])
        ), loop_id

        for service in binding["compose_services"]:
            assert service in compose_services, (
                f"{loop_id}: Compose service {service} does not set "
                "PANTHEON_CONTROLLER_NAME"
            )
            expected_compose[service] = controller_name

    # Reverse direction: every deployed controller is declared in the catalog.
    assert compose_services == expected_compose, (
        "Compose declares controller names that the loop catalog does not: "
        f"{sorted(set(compose_services) - set(expected_compose))}"
    )
    assert catalog_implemented == {
        loop_id: binding["controller_name"]
        for loop_id, binding in RUNTIME_CONTROLLER_BINDINGS.items()
    }


def test_loop_catalog_stops_at_implemented_until_hosted_evidence_is_admitted() -> None:
    """No committed loop may claim a proven_live controller contract."""

    registry = loop_inventory_model._load_registry()

    for loop in registry["loops"] + registry["composite_overlays"]:
        loop_id = loop["loop_id"]
        assert (
            loop["controller_contract"]["status"]
            not in CONTROLLER_STATUS_CEILING_FORBIDDEN
        ), f"{loop_id} claims a proven_live controller contract"


def test_loop_inventory_publishes_controller_contract_coverage(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    client = TestClient(bff_main.app, raise_server_exceptions=False)

    response = client.get("/bff/v5/loop-inventory", headers=HEADERS)

    assert response.status_code == 200, response.text
    payload = response.json()
    coverage = payload["meta"]["catalog"]["controller_contract_coverage"]

    assert coverage["declared_controller_loop_ids"] == list(RUNTIME_CONTROLLER_BINDINGS)
    assert coverage["declared_controller_count"] == len(RUNTIME_CONTROLLER_BINDINGS)
    assert coverage["no_declared_controller_count"] == 13 - len(
        RUNTIME_CONTROLLER_BINDINGS
    )
    assert coverage["incomplete_contract_loop_ids"] == []
    assert coverage["controller_names"] == {
        loop_id: binding["controller_name"]
        for loop_id, binding in RUNTIME_CONTROLLER_BINDINGS.items()
    }

    items = {item["loop_id"]: item for item in payload["items"]}
    declared = items["source_ingestion"]["controller_contract_declaration"]
    assert declared["status"] == "implemented"
    assert declared["controller_implemented"] is True
    assert declared["contract_complete"] is True
    assert declared["missing_contract_fields"] == []
    # A declared controller is still not liveness.
    assert items["source_ingestion"]["live_status"]["is_live"] is False
    assert items["source_ingestion"]["live_status"]["is_reconciled"] is False
    assert items["source_ingestion"]["live_status"]["has_live_evidence"] is False

    undeclared = items["consultation"]["controller_contract_declaration"]
    assert undeclared["status"] == "not_implemented"
    assert undeclared["controller_implemented"] is False
    assert undeclared["contract_complete"] is False


def test_loop_inventory_archive_completion_and_catalog_claim_do_not_create_liveness(
    monkeypatch,
) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    registry = deepcopy(loop_inventory_model._load_registry())
    source_loop = registry["loops"][0]
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
    monkeypatch.setattr(loop_inventory_model, "_load_registry", lambda: registry)
    client = TestClient(bff_main.app, raise_server_exceptions=False)

    response = client.get("/bff/v5/loop-inventory/source_ingestion", headers=HEADERS)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert "current_maturity" not in data
    assert "maturity_projection" not in data
    assert "execution_tasks" not in data
    assert data["live_status"]["has_live_evidence"] is False
    assert data["live_status"]["is_reconciled"] is False
    assert data["live_status"]["is_live"] is False
    assert data["live_status"]["reason"] == "catalog metadata is not live liveness proof"
