from __future__ import annotations

import ast
import json
from pathlib import Path

from fastapi.testclient import TestClient

from services.capital import main as capital_main
from services.capital.allocation_store import AllocationAuthorityStore
from services.capital.models import (
    CreateCapitalPoolRequest,
    PatchCapitalPoolRequest,
)
from services.capital.pg_store import (
    JsonlCapitalAuditStore,
    PersistentCapitalPoolStore,
)
from services.deployment.models import CreateDeploymentPlanRequest
from services.deployment.service import (
    DeploymentPlanStore,
    DeploymentPlannerService,
)
from services.persona.write_owner import (
    CreatePersonaRequest,
    PersistentPersonaOwner,
    create_app,
)
from services.runtime_manager import RuntimeManagerService


def test_persona_http_write_is_read_by_fresh_owner_instance(tmp_path: Path) -> None:
    store_path = tmp_path / "personas.json"
    first_app = create_app(PersistentPersonaOwner.from_json_path(store_path))

    created = TestClient(first_app).post(
        "/api/personas",
        json={
            "actor_id": "operator-persona",
            "persona_id": "persona-owner-proof",
            "name": "Owner Proof Persona",
            "mandate": "paper research only",
            "strategy_family": "factor",
            "required_data_sources": [
                {
                    "dataset": "tw_price_daily",
                    "market": "TW",
                    "cadence": "daily",
                    "source_class": "seed_only",
                }
            ],
        },
    )
    assert created.status_code == 201

    fresh_app = create_app(PersistentPersonaOwner.from_json_path(store_path))
    readback = TestClient(fresh_app).get("/api/personas/persona-owner-proof")

    assert readback.status_code == 200
    assert readback.json()["persona_id"] == "persona-owner-proof"
    assert readback.json()["required_data_sources"][0]["source_class"] == "seed_only"
    assert "persistenceMode" not in readback.json()


def _capital_service(tmp_path: Path) -> capital_main.CapitalBoundaryService:
    return capital_main.CapitalBoundaryService(
        pool_store=PersistentCapitalPoolStore(tmp_path / "capital_pools.json"),
        binding_store=capital_main.PersonaCapitalBindingStore(
            path=tmp_path / "persona_capital_bindings.json"
        ),
        allocation_store=AllocationAuthorityStore(
            path=tmp_path / "capital_allocation_authority.json"
        ),
        audit_log_path=tmp_path / "capital_audit.jsonl",
        audit_store=JsonlCapitalAuditStore(tmp_path / "capital_audit.jsonl"),
    )


def test_capital_patch_is_read_by_fresh_owner_store(tmp_path: Path) -> None:
    service = _capital_service(tmp_path)
    service.create_pool(
        CreateCapitalPoolRequest(
            actor_id="capital-operator",
            actor_role="capital.admin",
            pool_id="pool-owner-proof",
            name="Original Pool",
            owner_id="fund-owner-proof",
            owner_type="fund",
            status="active",
        )
    )

    patched = service.patch_pool(
        "pool-owner-proof",
        PatchCapitalPoolRequest(
            actor_id="capital-operator",
            actor_role="capital.admin",
            name="Persisted Pool",
            risk_policy_ref="risk-policy-v2",
            params={"paper_limit": 100000},
        ),
    )

    assert patched.name == "Persisted Pool"
    fresh = PersistentCapitalPoolStore(tmp_path / "capital_pools.json")
    readback = fresh.require("pool-owner-proof")
    assert readback.name == "Persisted Pool"
    assert readback.risk_policy_ref == "risk-policy-v2"
    assert readback.metadata["params"] == {"paper_limit": 100000}


def test_deployment_plan_write_is_read_by_fresh_owner_store(tmp_path: Path) -> None:
    plan_path = tmp_path / "deployment_plans.json"
    approval_path = tmp_path / "approval_decisions.json"
    registry_path = tmp_path / "registry_entries.json"
    approval_path.write_text(
        json.dumps(
            {
                "approval-owner-proof": {
                    "decision_id": "approval-owner-proof",
                    "target_id": "registry-owner-proof",
                    "target_version": "1.0.0",
                    "decision_state": "decided",
                    "decision": "approved",
                    "capital_pool_id": "pool-owner-proof",
                    "persona_id": "persona-owner-proof",
                    "tenant_id": "tenant-owner-proof",
                }
            }
        ),
        encoding="utf-8",
    )
    registry_path.write_text(
        json.dumps(
            {
                "registry-owner-proof": {
                    "registry_id": "registry-owner-proof",
                    "artifact_type": "model_artifact",
                    "strategy_id": "strategy-owner-proof",
                    "version": "1.0.0",
                    "artifact_state": "approved",
                    "checksum": "sha256:owner-proof",
                    "approval_decision_id": "approval-owner-proof",
                    "approved_at": "2026-08-29T00:00:00Z",
                    "lineage": {"source_run_ids": ["run-owner-proof"]},
                    "deployment_summary": {"current_stage": "none"},
                }
            }
        ),
        encoding="utf-8",
    )
    planner = DeploymentPlannerService(
        plan_store=DeploymentPlanStore(str(plan_path)),
        approval_store_path=approval_path,
        registry_snapshot_path=registry_path,
    )

    plan = planner.create_plan(
        CreateDeploymentPlanRequest(
            plan_id="plan-owner-proof",
            approval_decision_id="approval-owner-proof",
            registry_id="registry-owner-proof",
            capital_pool_id="pool-owner-proof",
            sponsor_persona_id="persona-owner-proof",
            target_stage="paper",
            rollback={
                "target_artifact_id": "registry-owner-proof-previous",
                "target_version": "0.9.0",
                "action_type": "replace",
            },
        ),
        persist=True,
        actor_id="deployment-operator",
        tenant_id="tenant-owner-proof",
    )

    fresh = DeploymentPlanStore(str(plan_path))
    readback = fresh.get(plan.plan_id)
    assert readback is not None
    assert readback.plan_id == "plan-owner-proof"
    assert readback.sponsor_persona_id == "persona-owner-proof"
    assert readback.target_stage == "paper"


def test_runtime_binding_write_is_read_by_fresh_manager(tmp_path: Path) -> None:
    store_path = tmp_path / "runtime_bindings.json"
    manager = RuntimeManagerService(store_path=store_path)
    binding = manager.deploy(
        {
            "plan_id": "plan-owner-proof",
            "plan_status": "approved",
            "target_stage": "paper",
            "artifact_id": "registry-owner-proof",
            "artifact_version": "1.0.0",
            "capital_pool_id": "pool-owner-proof",
            "persona_capital_binding_id": "binding-owner-proof",
            "persona_capital_binding_status": "active",
            "allowed_deployment_scope": "paper",
            "loader_checks_passed": True,
            "runtime_id": "runtime-owner-proof",
            "sponsor_persona_id": "persona-owner-proof",
        }
    )

    fresh = RuntimeManagerService(store_path=store_path)
    readback = fresh.get(binding.binding_id)
    assert readback is not None
    assert readback.runtime_id == "runtime-owner-proof"
    assert readback.plan_id == "plan-owner-proof"
    assert readback.persona_capital_binding_id == "binding-owner-proof"


def test_owner_modules_do_not_import_bff_read_store() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    owner_modules = (
        repository_root / "services/persona/write_owner.py",
        repository_root / "services/capital/main.py",
        repository_root / "services/runtime-manager/main.py",
        repository_root / "services/deployment/service.py",
    )
    for module_path in owner_modules:
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        assert not any(name == "read_store" or name.endswith(".read_store") for name in imported), module_path
