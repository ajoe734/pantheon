from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
import pytest

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


def _persona_headers(actor_id: str, *roles: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {actor_id}:{','.join(roles)}"}


def _persona_create_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
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
    }
    payload.update(overrides)
    return payload


def test_persona_http_write_is_read_by_fresh_owner_instance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PERSONA_AUTH_MODE", "permissive")
    store_path = tmp_path / "personas.json"
    first_app = create_app(PersistentPersonaOwner.from_json_path(store_path))

    created = TestClient(first_app).post(
        "/api/personas",
        json=_persona_create_payload(),
        headers=_persona_headers("operator-persona", "persona.admin"),
    )
    assert created.status_code == 201

    fresh_app = create_app(PersistentPersonaOwner.from_json_path(store_path))
    readback = TestClient(fresh_app).get("/api/personas/persona-owner-proof")

    assert readback.status_code == 200
    assert readback.json()["persona_id"] == "persona-owner-proof"
    assert readback.json()["required_data_sources"][0]["source_class"] == "seed_only"
    assert "persistenceMode" not in readback.json()


def test_persona_http_rejects_unauthenticated_direct_live_owner_create(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "personas.json"
    owner = PersistentPersonaOwner.from_json_path(store_path)
    client = TestClient(create_app(owner))

    response = client.post(
        "/api/personas",
        json=_persona_create_payload(lifecycle_state="live_owner"),
    )

    assert response.status_code == 401
    assert owner.list() == []


def test_persona_http_rejects_untrusted_role_and_spoofed_actor_create(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PERSONA_AUTH_MODE", "permissive")
    owner = PersistentPersonaOwner.from_json_path(tmp_path / "personas.json")
    client = TestClient(create_app(owner))

    wrong_role = client.post(
        "/api/personas",
        json=_persona_create_payload(actor_id="untrusted-operator"),
        headers=_persona_headers("untrusted-operator", "operator"),
    )
    spoofed_actor = client.post(
        "/api/personas",
        json=_persona_create_payload(actor_id="governance-actor"),
        headers=_persona_headers("persona-admin", "persona.admin"),
    )

    assert wrong_role.status_code == 403
    assert spoofed_actor.status_code == 403
    assert owner.list() == []


def test_persona_http_policy_owner_cannot_skip_directly_to_live_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PERSONA_AUTH_MODE", "permissive")
    owner = PersistentPersonaOwner.from_json_path(tmp_path / "personas.json")
    client = TestClient(create_app(owner))

    response = client.post(
        "/api/personas",
        json=_persona_create_payload(lifecycle_state="live_owner"),
        headers=_persona_headers("operator-persona", "persona.admin"),
    )

    assert response.status_code == 422
    assert owner.list() == []


def test_persona_http_untrusted_caller_cannot_self_promote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PERSONA_AUTH_MODE", "permissive")
    owner = PersistentPersonaOwner.from_json_path(tmp_path / "personas.json")
    client = TestClient(create_app(owner))
    admin_headers = _persona_headers("operator-persona", "persona.admin")
    assert client.post(
        "/api/personas",
        json=_persona_create_payload(),
        headers=admin_headers,
    ).status_code == 201
    assert client.patch(
        "/api/personas/persona-owner-proof/lifecycle",
        json={"actor_id": "operator-persona", "target_state": "research_only"},
        headers=admin_headers,
    ).status_code == 200

    unauthenticated = client.patch(
        "/api/personas/persona-owner-proof/lifecycle",
        json={"actor_id": "governance-actor", "target_state": "consultable"},
    )
    untrusted_role = client.patch(
        "/api/personas/persona-owner-proof/lifecycle",
        json={"actor_id": "untrusted-operator", "target_state": "consultable"},
        headers=_persona_headers("untrusted-operator", "operator"),
    )
    caller_supplied_decision = client.patch(
        "/api/personas/persona-owner-proof/lifecycle",
        json={
            "actor_id": "untrusted-operator",
            "target_state": "consultable",
            "governance_decision_id": "caller-invented-decision",
        },
        headers=_persona_headers("untrusted-operator", "operator"),
    )
    actor_spoof = client.patch(
        "/api/personas/persona-owner-proof/lifecycle",
        json={"actor_id": "governance-actor", "target_state": "consultable"},
        headers=_persona_headers("persona-admin", "persona.admin"),
    )
    patch_bypass = client.patch(
        "/api/personas/persona-owner-proof",
        json={"actor_id": "operator-persona", "lifecycle_state": "consultable"},
        headers=admin_headers,
    )

    assert unauthenticated.status_code == 401
    assert untrusted_role.status_code == 403
    assert caller_supplied_decision.status_code == 403
    assert actor_spoof.status_code == 403
    assert patch_bypass.status_code == 422
    assert owner.get("persona-owner-proof").lifecycle_state == "research_only"


def test_persona_http_governance_owner_controls_promotion_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PERSONA_AUTH_MODE", "permissive")
    owner = PersistentPersonaOwner.from_json_path(tmp_path / "personas.json")
    client = TestClient(create_app(owner))
    admin_headers = _persona_headers("operator-persona", "persona.admin")
    governance_headers = _persona_headers(
        "governance-actor",
        "governance_reviewer",
    )
    assert client.post(
        "/api/personas",
        json=_persona_create_payload(),
        headers=admin_headers,
    ).status_code == 201
    assert client.patch(
        "/api/personas/persona-owner-proof/lifecycle",
        json={"actor_id": "operator-persona", "target_state": "research_only"},
        headers=admin_headers,
    ).status_code == 200

    denied_persona_plane = client.patch(
        "/api/personas/persona-owner-proof/lifecycle",
        json={"actor_id": "operator-persona", "target_state": "consultable"},
        headers=admin_headers,
    )
    assert denied_persona_plane.status_code == 403

    for target_state in ("consultable", "paper_owner", "live_owner"):
        promoted = client.patch(
            "/api/personas/persona-owner-proof/lifecycle",
            json={"actor_id": "governance-actor", "target_state": target_state},
            headers=governance_headers,
        )
        assert promoted.status_code == 200
        assert promoted.json()["lifecycle_state"] == target_state


class _ExactGovernanceDecisionVerifier:
    def verify_persona_lifecycle_decision(
        self,
        *,
        decision_id: str,
        persona_id: str,
        source_state: str,
        target_state: str,
    ) -> bool:
        return (
            decision_id == "decision-consultable"
            and persona_id == "persona-owner-proof"
            and source_state == "research_only"
            and target_state == "consultable"
        )


def test_persona_http_decision_executor_requires_exact_governance_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PERSONA_AUTH_MODE", "permissive")
    owner = PersistentPersonaOwner.from_json_path(tmp_path / "personas.json")
    client = TestClient(
        create_app(
            owner,
            governance_decision_verifier=_ExactGovernanceDecisionVerifier(),
        )
    )
    admin_headers = _persona_headers("operator-persona", "persona.admin")
    assert client.post(
        "/api/personas",
        json=_persona_create_payload(),
        headers=admin_headers,
    ).status_code == 201
    assert client.patch(
        "/api/personas/persona-owner-proof/lifecycle",
        json={"actor_id": "operator-persona", "target_state": "research_only"},
        headers=admin_headers,
    ).status_code == 200

    wrong_decision = client.patch(
        "/api/personas/persona-owner-proof/lifecycle",
        json={
            "actor_id": "governance-executor",
            "target_state": "consultable",
            "governance_decision_id": "decision-wrong-target",
        },
        headers=_persona_headers("governance-executor", "operator"),
    )
    accepted = client.patch(
        "/api/personas/persona-owner-proof/lifecycle",
        json={
            "actor_id": "governance-executor",
            "target_state": "consultable",
            "governance_decision_id": "decision-consultable",
        },
        headers=_persona_headers("governance-executor", "operator"),
    )

    assert wrong_decision.status_code == 403
    assert accepted.status_code == 200
    assert accepted.json()["metadata"]["last_lifecycle_governance_decision_id"] == (
        "decision-consultable"
    )


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
