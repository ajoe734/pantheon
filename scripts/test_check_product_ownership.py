"""Unit and integration tests for scripts/check_product_ownership.py.

Tests the permanent architecture gate enforcing SD §3 and STRUCT-OWNERSHIP-001.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from scripts.check_product_ownership import (
    DEFAULT_COMPOSE,
    DEFAULT_MANIFEST,
    load_ownership_manifest,
    validate_aggregates,
    validate_mutation_routes,
    validate_symbol_dispositions,
    validate_worker_ownership,
    verify_product_ownership,
)


def test_load_ownership_manifest_exists_and_valid() -> None:
    manifest = load_ownership_manifest(DEFAULT_MANIFEST)
    assert isinstance(manifest, dict)
    assert manifest.get("version") == 1 or manifest.get("schema_version") == 1
    assert "aggregates" in manifest
    assert "mutation_routes" in manifest
    assert "workers" in manifest
    assert "symbol_dispositions" in manifest


def test_validate_aggregates_success() -> None:
    manifest = load_ownership_manifest(DEFAULT_MANIFEST)
    errors = validate_aggregates(manifest)
    assert not errors, f"Aggregate validation failed: {errors}"


def test_validate_aggregates_rejects_router_as_store_owner() -> None:
    sample = {
        "aggregates": {
            "test_bad": {
                "command_owner": "test_app",
                "store_owner": "test_router",
                "read_projection": "test_proj",
                "event_subjects": ["test.v1"],
                "bff_routers": ["test_router"],
                "forbidden_writers": [],
            }
        }
    }
    errors = validate_aggregates(sample)
    assert any("forbidden by SD §3.1" in err for err in errors)


def test_validate_aggregates_rejects_missing_owner() -> None:
    sample = {
        "aggregates": {
            "test_missing": {
                "command_owner": "",
                "store_owner": "test_store",
                "read_projection": "test_proj",
                "event_subjects": ["test.v1"],
                "bff_routers": ["test_router"],
                "forbidden_writers": [],
            }
        }
    }
    errors = validate_aggregates(sample)
    assert any("command_owner" in err for err in errors)


def test_validate_mutation_routes_rejects_duplicate_route() -> None:
    sample = {
        "mutation_routes": [
            {
                "route": "POST /bff/test",
                "command": "test_cmd",
                "router": "test",
                "application_owner": "test_app",
                "store_owner": "test_store",
                "table_or_stream": "test_table",
                "outbox_subject": "test.outbox",
                "idempotency_scope": "test_id",
                "readback_projection": "test_proj",
                "legacy_path": None,
                "legacy_removal_wave": None,
            },
            {
                "route": "POST /bff/test",
                "command": "test_cmd",
                "router": "test",
                "application_owner": "test_app",
                "store_owner": "test_store",
                "table_or_stream": "test_table",
                "outbox_subject": "test.outbox",
                "idempotency_scope": "test_id",
                "readback_projection": "test_proj",
                "legacy_path": None,
                "legacy_removal_wave": None,
            },
        ]
    }
    # Pass empty dummy app to avoid live mounted route mismatch check
    class DummyApp:
        routes: list = []

    errors = validate_mutation_routes(sample, live_app=DummyApp())
    assert any("Duplicate route" in err for err in errors)


@pytest.mark.parametrize("drift", ["omitted", "retired", "duplicate"])
def test_mutation_route_gate_rejects_inventory_drift(drift: str) -> None:
    """Neither a stale row nor a newly mounted route may be silently filtered."""
    from fastapi import APIRouter, FastAPI

    app = FastAPI()
    router = APIRouter()

    @router.post("/plans/{plan_id}")
    def create_plan(plan_id: str) -> dict[str, str]:
        return {"id": plan_id}

    app.include_router(router)
    row = {
        "route": "POST /plans/{param}",
        "command": "create_plan",
        "router": "plans",
        "application_owner": "plan_application",
        "store_owner": "plan_store",
        "table_or_stream": "plans",
        "outbox_subject": None,
        "idempotency_scope": "tenant:key",
        "readback_projection": "plan_detail",
        "legacy_path": None,
        "legacy_removal_wave": None,
    }
    assert validate_mutation_routes({"mutation_routes": [row]}, live_app=app) == []
    if drift == "omitted":
        rows = [{**row, "route": "POST /different-plan"}]
        expected = "Mounted mutation routes missing from inventory"
    elif drift == "retired":
        rows = [row, {**row, "route": "POST /retired-plan"}]
        expected = "Inventory declares mutation routes not mounted"
    else:
        rows = [row, dict(row)]
        expected = "Duplicate route"
    errors = validate_mutation_routes({"mutation_routes": rows}, live_app=app)
    assert any(expected in error for error in errors), errors


def test_servant_proposal_inventory_describes_draft_persistence_not_dispatch() -> None:
    routes = load_ownership_manifest(DEFAULT_MANIFEST)["mutation_routes"]
    proposal = next(row for row in routes if row["route"] == (
        "POST /bff/agora/workshops/{param}/research-plans/servant-proposal"
    ))
    assert proposal["application_owner"] == "AgoraResearchService.create_workshop_plan"
    assert proposal["store_owner"] == "agora.research.store.make_research_plan_store"
    assert proposal["outbox_subject"] is None
    assert proposal["idempotency_scope"] == "user_id:tenant_id:endpoint:Idempotency-Key"


def test_validate_worker_ownership_rejects_duplicate_lease() -> None:
    sample = {
        "workers": [
            {
                "service": "w1",
                "profile": None,
                "input_subject": "sub.1",
                "durable_consumer": "w1",
                "lease_key": "lease:shared",
                "partition_policy": "singleton",
                "output_subject": "out.1",
                "retry_policy": "exp",
                "dlq": "dlq.1",
                "readiness_probe": "/readyz",
            },
            {
                "service": "w2",
                "profile": None,
                "input_subject": "sub.2",
                "durable_consumer": "w2",
                "lease_key": "lease:shared",
                "partition_policy": "singleton",
                "output_subject": "out.2",
                "retry_policy": "exp",
                "dlq": "dlq.2",
                "readiness_probe": "/readyz",
            },
        ]
    }
    errors = validate_worker_ownership(sample, compose_path=Path("/dev/null"))
    assert any("Duplicate lease_key" in err for err in errors)


def test_validate_worker_ownership_rejects_unpartitioned_collision() -> None:
    sample = {
        "workers": [
            {
                "service": "w1",
                "profile": None,
                "input_subject": "sub.shared",
                "durable_consumer": "w1",
                "lease_key": "lease:w1",
                "partition_policy": "singleton",
                "output_subject": "out.1",
                "retry_policy": "exp",
                "dlq": "dlq.1",
                "readiness_probe": "/readyz",
            },
            {
                "service": "w2",
                "profile": None,
                "input_subject": "sub.shared",
                "durable_consumer": "w2",
                "lease_key": "lease:w2",
                "partition_policy": "singleton",
                "output_subject": "out.2",
                "retry_policy": "exp",
                "dlq": "dlq.2",
                "readiness_probe": "/readyz",
            },
        ]
    }
    errors = validate_worker_ownership(sample, compose_path=Path("/dev/null"))
    assert any("Subject collision on 'sub.shared'" in err for err in errors)


def test_validate_symbol_dispositions_rejects_forbidden_disposition() -> None:
    sample = {
        "symbol_dispositions": [
            {
                "symbol": "compat_sym",
                "locations": ["test.py:1"],
                "canonical_owner": "test.py",
                "disposition": "COMPATIBILITY_FOREVER",
                "production_callers_before": 1,
                "production_callers_after": 1,
                "deletion_wave": "never",
            }
        ]
    }
    errors = validate_symbol_dispositions(sample)
    assert any("forbidden disposition" in err for err in errors)


def test_verify_product_ownership_live_passes() -> None:
    result = verify_product_ownership(manifest_path=DEFAULT_MANIFEST, compose_path=DEFAULT_COMPOSE)
    assert result["valid"], f"Product ownership verification failed:\n" + "\n".join(result["errors"])
    assert result["stats"]["aggregates_count"] >= 20
    assert result["stats"]["mutation_routes_count"] >= 200
    assert result["stats"]["workers_count"] >= 20
    assert result["stats"]["symbol_dispositions_count"] == 225
