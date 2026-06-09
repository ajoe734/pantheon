"""
MPOS-P1-ART-001: Tests for AllocationPolicyArtifact registry facade.

Covers:
- Registration via POST /api/registry/allocation-policy-artifacts
- Lineage extraction: provenance_refs -> source_run_ids,
  conflict_resolution_log_id -> source_strategy_spec_id
- Evaluation summary carries synthesis evidence
- Artifact_type is allocation_policy
- Advance lifecycle: candidate -> approved (required for DeploymentPlan reference)
- GET allocation-policy-artifact by registry_id
- Pool-scoped list: GET /api/registry/pools/{pool}/allocation-policy-artifacts
- Type guard: GET 404 for non-allocation-policy registry_id
- Validation errors for missing required fields
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from .models import ArtifactState, ArtifactType
from .service import app
from .storage import reset_store


# -- Fixtures -----------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_store():
    reset_store()
    yield
    reset_store()


client = TestClient(app)


def _minimal_artifact(
    artifact_id: str = "artifact-001",
    capital_pool_id: str = "pool-alpha",
    scope_ref: str = "paper",
    provenance_refs: list | None = None,
    conflict_log_id: str = "log-001",
    risk_budget: float | None = None,
) -> dict:
    artifact: dict = {
        "artifact_id": artifact_id,
        "capital_pool_id": capital_pool_id,
        "scope_ref": scope_ref,
        "sponsor_persona_id": "persona-momentum",
        "synthesis_method": "weighted_fusion",
        "target_weights": {"SPY": 0.6, "QQQ": 0.4},
        "created_at": "2026-06-01T12:00:00Z",
        "provenance_refs": ["prop-001", "prop-002"] if provenance_refs is None else provenance_refs,
        "conflict_resolution_log_id": conflict_log_id,
    }
    if risk_budget is not None:
        artifact["risk_budget"] = risk_budget
    return artifact


def _register_payload(
    version: str = "1.0.0",
    artifact: dict | None = None,
    registry_id: str | None = None,
) -> dict:
    return {
        "version": version,
        "allocation_policy_artifact": artifact or _minimal_artifact(),
        **({"registry_id": registry_id} if registry_id else {}),
    }


# -- Registration -------------------------------------------------------------

class TestAllocationPolicyRegistration:
    def test_register_returns_candidate_allocation_policy(self):
        resp = client.post(
            "/api/registry/allocation-policy-artifacts",
            json=_register_payload(),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["entry"]["artifact_type"] == "allocation_policy"
        assert data["entry"]["artifact_state"] == "candidate"
        assert data["entry"]["strategy_id"] == "pool-alpha"

    def test_register_lineage_provenance_refs(self):
        resp = client.post(
            "/api/registry/allocation-policy-artifacts",
            json=_register_payload(artifact=_minimal_artifact(provenance_refs=["p-001", "p-002", "p-003"])),
        )
        assert resp.status_code == 200, resp.text
        lineage = resp.json()["entry"]["lineage"]
        assert lineage["source_run_ids"] == ["p-001", "p-002", "p-003"]

    def test_register_lineage_conflict_log_id(self):
        resp = client.post(
            "/api/registry/allocation-policy-artifacts",
            json=_register_payload(artifact=_minimal_artifact(conflict_log_id="log-conflict-42")),
        )
        assert resp.status_code == 200, resp.text
        lineage = resp.json()["entry"]["lineage"]
        assert lineage["source_strategy_spec_id"] == "log-conflict-42"

    def test_register_evaluation_summary_carries_synthesis_evidence(self):
        artifact = _minimal_artifact(scope_ref="canary", risk_budget=0.15)
        resp = client.post(
            "/api/registry/allocation-policy-artifacts",
            json=_register_payload(artifact=artifact),
        )
        assert resp.status_code == 200, resp.text
        ev = resp.json()["entry"]["evaluation_summary"]
        assert ev["conflict_resolution_log_id"] == artifact["conflict_resolution_log_id"]
        assert ev["synthesis_method"] == "weighted_fusion"
        assert ev["sponsor_persona_id"] == "persona-momentum"
        assert ev["scope_ref"] == "canary"
        assert ev["risk_budget"] == pytest.approx(0.15)

    def test_register_checksum_computed_when_omitted(self):
        resp = client.post(
            "/api/registry/allocation-policy-artifacts",
            json=_register_payload(),
        )
        assert resp.status_code == 200, resp.text
        checksum = resp.json()["entry"]["checksum"]
        assert checksum.startswith("sha256:")

    def test_register_custom_checksum_accepted(self):
        payload = _register_payload()
        payload["checksum"] = "sha256:deadbeef1234567890abcdef"
        resp = client.post("/api/registry/allocation-policy-artifacts", json=payload)
        assert resp.status_code == 200, resp.text
        assert resp.json()["entry"]["checksum"] == "sha256:deadbeef1234567890abcdef"

    def test_register_producer_run_id_equals_artifact_id(self):
        resp = client.post(
            "/api/registry/allocation-policy-artifacts",
            json=_register_payload(artifact=_minimal_artifact(artifact_id="my-artifact-99")),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["entry"]["producer_run_id"] == "my-artifact-99"

    def test_register_artifact_stored_inline_in_metadata(self):
        artifact = _minimal_artifact()
        resp = client.post(
            "/api/registry/allocation-policy-artifacts",
            json=_register_payload(artifact=artifact),
        )
        assert resp.status_code == 200, resp.text
        stored = resp.json()["entry"]["metadata"]["allocation_policy_artifact"]
        assert stored["artifact_id"] == artifact["artifact_id"]
        assert stored["target_weights"] == artifact["target_weights"]

    def test_register_explicit_registry_id_accepted(self):
        resp = client.post(
            "/api/registry/allocation-policy-artifacts",
            json=_register_payload(registry_id="reg-alloc-test-001"),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["entry"]["registry_id"] == "reg-alloc-test-001"


# -- Lifecycle ----------------------------------------------------------------

class TestAllocationPolicyLifecycle:
    def test_advance_candidate_to_approved(self):
        reg_resp = client.post(
            "/api/registry/allocation-policy-artifacts",
            json=_register_payload(registry_id="reg-alloc-lc-001"),
        )
        assert reg_resp.status_code == 200

        adv_resp = client.post(
            "/api/registry/allocation-policy-artifacts/reg-alloc-lc-001/advance",
            json={"target_state": "approved", "approver": "governance-bot"},
        )
        assert adv_resp.status_code == 200, adv_resp.text
        assert adv_resp.json()["entry"]["artifact_state"] == "approved"
        assert adv_resp.json()["entry"]["approver"] == "governance-bot"

    def test_approved_entry_is_deployment_plan_ready(self):
        """approved allocation_policy artifact satisfies DeploymentPlan artifact_state gate."""
        client.post(
            "/api/registry/allocation-policy-artifacts",
            json=_register_payload(registry_id="reg-alloc-dep-001"),
        )
        adv_resp = client.post(
            "/api/registry/allocation-policy-artifacts/reg-alloc-dep-001/advance",
            json={"target_state": "approved", "approver": "ops"},
        )
        entry = adv_resp.json()["entry"]
        # DeploymentPlan.create_plan() requires artifact_state == 'approved'
        assert entry["artifact_state"] == "approved"
        assert entry["artifact_type"] == "allocation_policy"
        # strategy_id == capital_pool_id so DeploymentPlan.capital_pool_id can match
        assert entry["strategy_id"] == "pool-alpha"


# -- Read endpoints -----------------------------------------------------------

class TestAllocationPolicyRead:
    def test_get_by_registry_id(self):
        client.post(
            "/api/registry/allocation-policy-artifacts",
            json=_register_payload(registry_id="reg-alloc-get-001"),
        )
        resp = client.get("/api/registry/allocation-policy-artifacts/reg-alloc-get-001")
        assert resp.status_code == 200, resp.text
        assert resp.json()["entry"]["registry_id"] == "reg-alloc-get-001"

    def test_get_wrong_type_returns_404(self):
        # Register a different artifact type via the generic endpoint
        import uuid
        generic_id = f"reg-generic-{uuid.uuid4().hex[:8]}"
        client.post(
            "/api/registry/entries",
            json={
                "artifact_type": "model_artifact",
                "strategy_id": "strat-x",
                "version": "1.0.0",
                "artifact_state": "draft",
                "lineage": {"source_run_ids": ["run-001"]},
                "storage_ref": {"backend": "object_store", "path": "s3://bucket/a.bin"},
                "checksum": "sha256:abc123",
            },
        )
        # Attempting to read it via the alloc-policy endpoint should return 404
        all_entries = client.get("/api/registry/strategies/strat-x/entries").json()
        if all_entries:
            actual_id = all_entries[0]["entry"]["registry_id"]
            resp = client.get(f"/api/registry/allocation-policy-artifacts/{actual_id}")
            assert resp.status_code == 404

    def test_list_by_pool(self):
        client.post(
            "/api/registry/allocation-policy-artifacts",
            json=_register_payload(version="1.0.0", artifact=_minimal_artifact(capital_pool_id="pool-beta")),
        )
        client.post(
            "/api/registry/allocation-policy-artifacts",
            json=_register_payload(version="1.1.0", artifact=_minimal_artifact(capital_pool_id="pool-beta")),
        )
        resp = client.get("/api/registry/pools/pool-beta/allocation-policy-artifacts")
        assert resp.status_code == 200, resp.text
        entries = resp.json()
        assert len(entries) == 2
        for entry in entries:
            assert entry["entry"]["artifact_type"] == "allocation_policy"
            assert entry["entry"]["strategy_id"] == "pool-beta"

    def test_list_by_pool_filter_by_state(self):
        client.post(
            "/api/registry/allocation-policy-artifacts",
            json=_register_payload(version="1.0.0", registry_id="reg-filter-001",
                                   artifact=_minimal_artifact(capital_pool_id="pool-gamma")),
        )
        client.post(
            "/api/registry/allocation-policy-artifacts",
            json=_register_payload(version="1.1.0", registry_id="reg-filter-002",
                                   artifact=_minimal_artifact(capital_pool_id="pool-gamma")),
        )
        client.post(
            "/api/registry/allocation-policy-artifacts/reg-filter-001/advance",
            json={"target_state": "approved", "approver": "ops"},
        )

        approved = client.get(
            "/api/registry/pools/pool-gamma/allocation-policy-artifacts",
            params={"artifact_state": "approved"},
        ).json()
        assert len(approved) == 1
        assert approved[0]["entry"]["registry_id"] == "reg-filter-001"

        candidate = client.get(
            "/api/registry/pools/pool-gamma/allocation-policy-artifacts",
            params={"artifact_state": "candidate"},
        ).json()
        assert len(candidate) == 1
        assert candidate[0]["entry"]["registry_id"] == "reg-filter-002"


# -- Validation ---------------------------------------------------------------

class TestAllocationPolicyValidation:
    def test_missing_artifact_id_rejected(self):
        artifact = _minimal_artifact()
        del artifact["artifact_id"]
        resp = client.post(
            "/api/registry/allocation-policy-artifacts",
            json=_register_payload(artifact=artifact),
        )
        assert resp.status_code == 400

    def test_missing_provenance_refs_rejected(self):
        artifact = _minimal_artifact()
        del artifact["provenance_refs"]
        resp = client.post(
            "/api/registry/allocation-policy-artifacts",
            json=_register_payload(artifact=artifact),
        )
        assert resp.status_code == 400

    def test_empty_provenance_refs_rejected(self):
        artifact = _minimal_artifact(provenance_refs=[])
        resp = client.post(
            "/api/registry/allocation-policy-artifacts",
            json=_register_payload(artifact=artifact),
        )
        assert resp.status_code == 400

    def test_invalid_synthesis_method_rejected(self):
        artifact = _minimal_artifact()
        artifact["synthesis_method"] = "magic_oracle"
        resp = client.post(
            "/api/registry/allocation-policy-artifacts",
            json=_register_payload(artifact=artifact),
        )
        assert resp.status_code == 400

    def test_missing_target_weights_rejected(self):
        artifact = _minimal_artifact()
        del artifact["target_weights"]
        resp = client.post(
            "/api/registry/allocation-policy-artifacts",
            json=_register_payload(artifact=artifact),
        )
        assert resp.status_code == 400

    def test_missing_conflict_log_id_rejected(self):
        artifact = _minimal_artifact()
        del artifact["conflict_resolution_log_id"]
        resp = client.post(
            "/api/registry/allocation-policy-artifacts",
            json=_register_payload(artifact=artifact),
        )
        assert resp.status_code == 400

    def test_invalid_semver_version_rejected(self):
        resp = client.post(
            "/api/registry/allocation-policy-artifacts",
            json=_register_payload(version="not-a-semver"),
        )
        assert resp.status_code == 400
