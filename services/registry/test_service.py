"""
BP5-SVC-002: Smoke tests for the split artifact_state / deployment_stage registry service.

Covers:
- Artifact-state transitions and forbidden transitions
- Registry entry CRUD
- resolve_latest_approved with semver ordering
- resolve_deployment_view composition
- Deployment summary projection (non-authoritative)
- FastAPI endpoint integration via TestClient
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from .models import (
    ArtifactState,
    DeploymentStage,
    DeploymentSummary,
    Lineage,
    RegistryEntryCreate,
    StorageBackend,
    StorageRef,
)
from .service import app
from .split_api import RegistryError, RegistryNotFoundError, RegistryService
from .storage import RegistryStore, reset_store


# -- Fixtures -------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_store():
    """Reset the singleton store before each test."""
    reset_store()
    yield
    reset_store()


def _make_create_payload(
    strategy_id: str = "test-alpha",
    version: str = "1.0.0",
    artifact_state: ArtifactState = ArtifactState.DRAFT,
) -> RegistryEntryCreate:
    return RegistryEntryCreate(
        artifact_type="model_artifact",
        strategy_id=strategy_id,
        version=version,
        artifact_state=artifact_state,
        lineage=Lineage(source_run_ids=["run-001"]),
        storage_ref=StorageRef(backend=StorageBackend.OBJECT_STORE, path="s3://bucket/artifact.bin"),
        checksum="sha256:abc123",
    )


def _minimal_strategy_spec(strategy_id: str = "strat-distilled") -> dict:
    return {
        "spec_version": "1.0",
        "strategy_id": strategy_id,
        "title": "Distilled momentum hypothesis",
        "hypothesis": "A bounded source seed suggests a momentum continuation effect.",
        "objective": "Create a governed StrategySpec artifact for research orchestration.",
        "market_scope": {"symbols": ["SPY"], "frequency": "1d"},
        "data_dependencies": [{"ref": "source-seed-001", "kind": "note"}],
        "execution_profile": {
            "signal_schema_version": "research-signal-v1",
            "quantity_type": "PERCENT_PORTFOLIO",
            "execution_mode_hint": "research",
        },
        "evaluation_plan": {"metrics": ["sharpe_ratio"], "candidate_gate": "schema-valid"},
        "governance": {"approval_required": True, "risk_profile": "research"},
        "provenance": {
            "source_kind": "workflow",
            "created_at": "2026-05-16T07:00:00Z",
            "source_refs": ["source-seed-001"],
            "created_by": "test",
        },
    }


# -- Model unit tests -----------------------------------------------------

class TestArtifactStateTransitions:
    def test_artifact_state_rejects_deployment_stage_values(self):
        for deployment_stage in ["paper", "canary", "live"]:
            with pytest.raises(ValueError, match=deployment_stage):
                RegistryEntryCreate(
                    artifact_type="model_artifact",
                    strategy_id="test-alpha",
                    version="1.0.0",
                    artifact_state=deployment_stage,
                    lineage=Lineage(source_run_ids=["run-001"]),
                    storage_ref=StorageRef(
                        backend=StorageBackend.OBJECT_STORE,
                        path="s3://bucket/artifact.bin",
                    ),
                    checksum="sha256:abc123",
                )

    def test_draft_to_candidate(self):
        store = RegistryStore()
        svc = RegistryService(store)
        entry = svc.register(_make_create_payload(), "reg-001")
        assert entry.entry.artifact_state == ArtifactState.DRAFT

        advanced = svc.advance_artifact_state("reg-001", ArtifactState.CANDIDATE)
        assert advanced.entry.artifact_state == ArtifactState.CANDIDATE

    def test_candidate_to_approved(self):
        store = RegistryStore()
        svc = RegistryService(store)
        entry = svc.register(_make_create_payload(), "reg-001")
        svc.advance_artifact_state("reg-001", ArtifactState.CANDIDATE)
        advanced = svc.advance_artifact_state("reg-001", ArtifactState.APPROVED, approver="risk-committee")
        assert advanced.entry.artifact_state == ArtifactState.APPROVED
        assert advanced.entry.approver == "risk-committee"
        assert advanced.entry.approved_at is not None

    def test_approval_requires_lineage(self):
        store = RegistryStore()
        svc = RegistryService(store)
        svc.register(
            RegistryEntryCreate(
                artifact_type="model_artifact",
                strategy_id="test-alpha",
                version="1.0.0",
                lineage=Lineage(),
                storage_ref=StorageRef(backend=StorageBackend.OBJECT_STORE, path="s3://bucket/artifact.bin"),
                checksum="sha256:abc123",
            ),
            "reg-001",
        )
        svc.advance_artifact_state("reg-001", ArtifactState.CANDIDATE)
        with pytest.raises(RegistryError, match="Cannot approve artifact without lineage"):
            svc.advance_artifact_state("reg-001", ArtifactState.APPROVED)

    def test_approved_to_retired(self):
        store = RegistryStore()
        svc = RegistryService(store)
        entry = svc.register(_make_create_payload(), "reg-001")
        svc.advance_artifact_state("reg-001", ArtifactState.CANDIDATE)
        svc.advance_artifact_state("reg-001", ArtifactState.APPROVED)
        advanced = svc.advance_artifact_state("reg-001", ArtifactState.RETIRED)
        assert advanced.entry.artifact_state == ArtifactState.RETIRED

    def test_draft_to_retired(self):
        store = RegistryStore()
        svc = RegistryService(store)
        svc.register(_make_create_payload(), "reg-001")
        advanced = svc.advance_artifact_state("reg-001", ArtifactState.RETIRED)
        assert advanced.entry.artifact_state == ArtifactState.RETIRED

    def test_candidate_to_retired(self):
        store = RegistryStore()
        svc = RegistryService(store)
        entry = svc.register(_make_create_payload(), "reg-001")
        svc.advance_artifact_state("reg-001", ArtifactState.CANDIDATE)
        advanced = svc.advance_artifact_state("reg-001", ArtifactState.RETIRED)
        assert advanced.entry.artifact_state == ArtifactState.RETIRED

    def test_forbidden_transition_draft_to_approved(self):
        store = RegistryStore()
        svc = RegistryService(store)
        svc.register(_make_create_payload(), "reg-001")
        with pytest.raises(RegistryError, match="Forbidden"):
            svc.advance_artifact_state("reg-001", ArtifactState.APPROVED)

    def test_forbidden_transition_draft_to_retired_via_candidate(self):
        """Draft can go directly to retired, but not to approved without candidate."""
        store = RegistryStore()
        svc = RegistryService(store)
        svc.register(_make_create_payload(), "reg-001")
        # Direct draft->retired IS allowed, so let's test retired->draft (impossible)
        svc.advance_artifact_state("reg-001", ArtifactState.RETIRED)
        with pytest.raises(RegistryError, match="Forbidden"):
            svc.advance_artifact_state("reg-001", ArtifactState.DRAFT)

    def test_forbidden_transition_retired_to_anything(self):
        store = RegistryStore()
        svc = RegistryService(store)
        svc.register(_make_create_payload(), "reg-001")
        svc.advance_artifact_state("reg-001", ArtifactState.RETIRED)
        for target in [ArtifactState.DRAFT, ArtifactState.CANDIDATE, ArtifactState.APPROVED]:
            with pytest.raises(RegistryError, match="Forbidden"):
                svc.advance_artifact_state("reg-001", target)

    def test_approved_does_not_change_deployment_stage(self):
        """Approving an artifact does NOT set deployment_stage."""
        store = RegistryStore()
        svc = RegistryService(store)
        svc.register(_make_create_payload(), "reg-001")
        svc.advance_artifact_state("reg-001", ArtifactState.CANDIDATE)
        approved = svc.advance_artifact_state("reg-001", ArtifactState.APPROVED)
        assert approved.deployment_stage == DeploymentStage.NONE


# -- Storage tests --------------------------------------------------------

class TestStorage:
    def test_create_and_get(self):
        store = RegistryStore()
        entry = store.create(_make_create_payload(), "reg-001")
        got = store.get("reg-001")
        assert got is not None
        assert got.registry_id == "reg-001"

    def test_get_not_found(self):
        store = RegistryStore()
        assert store.get("nonexistent") is None

    def test_list_by_strategy(self):
        store = RegistryStore()
        store.create(_make_create_payload(version="1.0.0"), "reg-001")
        store.create(_make_create_payload(version="1.1.0"), "reg-002")
        store.create(_make_create_payload(strategy_id="other", version="1.0.0"), "reg-003")
        entries = store.list_by_strategy("test-alpha")
        assert len(entries) == 2

    def test_resolve_latest_approved_none(self):
        store = RegistryStore()
        store.create(_make_create_payload(), "reg-001")  # draft
        assert store.resolve_latest_approved("test-alpha") is None

    def test_resolve_latest_approved_semver(self):
        store = RegistryStore()
        payload_1 = _make_create_payload(version="1.0.0")
        payload_2 = _make_create_payload(version="2.0.0")
        payload_3 = _make_create_payload(version="1.5.0")

        store.create(payload_1, "reg-001")
        store.create(payload_2, "reg-002")
        store.create(payload_3, "reg-003")

        # Approve all
        for rid in ["reg-001", "reg-002", "reg-003"]:
            store.get(rid).artifact_state = ArtifactState.APPROVED
            store.update(store.get(rid))

        latest = store.resolve_latest_approved("test-alpha")
        assert latest is not None
        assert latest.version == "2.0.0"

    def test_deployment_summary_update(self):
        store = RegistryStore()
        store.create(_make_create_payload(), "reg-001")
        updated = store.update_deployment_summary(
            "reg-001",
            current_stage=DeploymentStage.PAPER,
            deployment_plan_id="plan-001",
        )
        assert updated is not None
        assert updated.deployment_summary is not None
        assert updated.deployment_summary.current_stage == DeploymentStage.PAPER
        assert updated.deployment_summary.deployment_plan_id == "plan-001"


# -- Deployment view tests ------------------------------------------------

class TestDeploymentView:
    def test_empty_strategy(self):
        store = RegistryStore()
        svc = RegistryService(store)
        view = svc.resolve_deployment_view("nonexistent")
        assert view.current_stage == DeploymentStage.NONE

    def test_approved_without_deployment_summary(self):
        store = RegistryStore()
        svc = RegistryService(store)
        svc.register(_make_create_payload(), "reg-001")
        svc.advance_artifact_state("reg-001", ArtifactState.CANDIDATE)
        svc.advance_artifact_state("reg-001", ArtifactState.APPROVED)

        view = svc.resolve_deployment_view("test-alpha")
        assert view.current_stage == DeploymentStage.NONE
        assert view.latest_approved_registry_id == "reg-001"

    def test_approved_with_deployment_summary(self):
        store = RegistryStore()
        svc = RegistryService(store)
        svc.register(_make_create_payload(), "reg-001")
        svc.advance_artifact_state("reg-001", ArtifactState.CANDIDATE)
        svc.advance_artifact_state("reg-001", ArtifactState.APPROVED)

        # Simulate deployment service updating the projection
        svc.update_deployment_summary(
            "reg-001",
            current_stage=DeploymentStage.CANARY,
            deployment_plan_id="plan-001",
            runtime_binding_id="rb-001",
        )

        view = svc.resolve_deployment_view("test-alpha")
        assert view.current_stage == DeploymentStage.CANARY
        assert view.deployment_plan_id == "plan-001"
        assert view.runtime_binding_id == "rb-001"

    def test_deployment_stage_requires_approved_artifact(self):
        store = RegistryStore()
        svc = RegistryService(store)
        svc.register(_make_create_payload(), "reg-001")
        with pytest.raises(
            RegistryError,
            match="Cannot project a non-'none' deployment stage onto an artifact that is not approved",
        ):
            svc.update_deployment_summary("reg-001", current_stage=DeploymentStage.PAPER)


# -- FastAPI integration tests --------------------------------------------

class TestFastAPIEndpoints:
    def setup_method(self):
        self.client = TestClient(app)

    def test_health(self):
        resp = self.client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_register_entry(self):
        payload = {
            "artifact_type": "model_artifact",
            "strategy_id": "api-test",
            "version": "1.0.0",
            "storage_ref": {"backend": "object_store", "path": "s3://bucket/art.bin"},
            "checksum": "sha256:xyz",
            "lineage": {"source_run_ids": ["run-001"]},
        }
        resp = self.client.post("/api/registry/entries", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["entry"]["artifact_state"] == "draft"
        assert data["deployment_stage"] == "none"
        assert data["entry"]["strategy_id"] == "api-test"

    def test_register_strategy_spec_from_source_seed_inline_payload(self):
        payload = {
            "strategy_id": "strat-distilled",
            "version": "1.0.0",
            "source_seed_id": "source-seed-001",
            "strategy_spec": _minimal_strategy_spec("strat-distilled"),
        }

        resp = self.client.post("/api/registry/strategy-specs", json=payload)

        assert resp.status_code == 200, resp.text
        data = resp.json()
        entry = data["entry"]
        assert entry["artifact_type"] == "strategy_spec"
        assert entry["artifact_state"] == "draft"
        assert data["deployment_stage"] == "none"
        assert entry["lineage"]["source_run_ids"] == ["source-seed-001"]
        assert entry["storage_ref"] == {
            "backend": "inline",
            "path": "$.entry.metadata.strategy_spec",
        }
        assert entry["checksum"].startswith("sha256:")
        assert entry["metadata"]["source_seed_id"] == "source-seed-001"
        assert entry["metadata"]["strategy_spec"]["title"] == "Distilled momentum hypothesis"

    def test_strategy_spec_facade_lists_gets_and_advances_only_strategy_specs(self):
        strategy_id = "strat-distilled"
        create_resp = self.client.post(
            "/api/registry/strategy-specs",
            json={
                "strategy_id": strategy_id,
                "version": "1.0.0",
                "source_seed_id": "source-seed-001",
                "strategy_spec": _minimal_strategy_spec(strategy_id),
            },
        )
        assert create_resp.status_code == 200, create_resp.text
        registry_id = create_resp.json()["entry"]["registry_id"]

        self.client.post("/api/registry/entries", json={
            "artifact_type": "model_artifact",
            "strategy_id": strategy_id,
            "version": "2.0.0",
            "storage_ref": {"backend": "object_store", "path": "s3://bucket/model.bin"},
            "checksum": "sha256:model",
            "lineage": {"source_run_ids": ["run-model"]},
        })

        list_resp = self.client.get(f"/api/registry/strategies/{strategy_id}/strategy-specs")
        assert list_resp.status_code == 200, list_resp.text
        assert [item["entry"]["registry_id"] for item in list_resp.json()] == [registry_id]

        get_resp = self.client.get(f"/api/registry/strategy-specs/{registry_id}")
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["entry"]["artifact_type"] == "strategy_spec"

        advance_resp = self.client.post(
            f"/api/registry/strategy-specs/{registry_id}/advance",
            json={"target_state": "candidate"},
        )
        assert advance_resp.status_code == 200, advance_resp.text
        assert advance_resp.json()["entry"]["artifact_state"] == "candidate"

        filtered = self.client.get(
            f"/api/registry/strategies/{strategy_id}/strategy-specs?artifact_state=candidate"
        )
        assert filtered.status_code == 200, filtered.text
        assert [item["entry"]["registry_id"] for item in filtered.json()] == [registry_id]

    def test_strategy_spec_facade_rejects_missing_lineage(self):
        resp = self.client.post(
            "/api/registry/strategy-specs",
            json={
                "strategy_id": "strat-no-lineage",
                "version": "1.0.0",
                "storage_ref": {"backend": "object_store", "path": "s3://bucket/spec.json"},
                "checksum": "sha256:spec",
            },
        )

        assert resp.status_code == 400, resp.text
        assert "require lineage" in resp.json()["detail"]

    def test_strategy_spec_facade_rejects_mismatched_inline_strategy_id(self):
        resp = self.client.post(
            "/api/registry/strategy-specs",
            json={
                "strategy_id": "strat-request",
                "version": "1.0.0",
                "source_seed_id": "source-seed-001",
                "strategy_spec": _minimal_strategy_spec("strat-inline"),
            },
        )

        assert resp.status_code == 400, resp.text
        assert "must match" in resp.json()["detail"]

    def test_register_rejects_deployment_stage_as_artifact_state(self):
        for deployment_stage in ["paper", "canary", "live"]:
            resp = self.client.post("/api/registry/entries", json={
                "artifact_type": "model_artifact",
                "strategy_id": f"api-test-{deployment_stage}",
                "version": "1.0.0",
                "artifact_state": deployment_stage,
                "storage_ref": {"backend": "object_store", "path": "s3://bucket/art.bin"},
                "checksum": "sha256:xyz",
                "lineage": {"source_run_ids": ["run-001"]},
            })
            assert resp.status_code == 422
            assert deployment_stage in resp.text

    def test_get_entry(self):
        # First create
        payload = {
            "artifact_type": "strategy_spec",
            "strategy_id": "api-test",
            "version": "1.0.0",
            "storage_ref": {"backend": "object_store", "path": "s3://bucket/art.bin"},
            "checksum": "sha256:xyz",
        }
        create_resp = self.client.post("/api/registry/entries", json=payload)
        registry_id = create_resp.json()["entry"]["registry_id"]

        # Then get
        resp = self.client.get(f"/api/registry/entries/{registry_id}")
        assert resp.status_code == 200
        assert resp.json()["entry"]["registry_id"] == registry_id

    def test_get_entry_not_found(self):
        resp = self.client.get("/api/registry/entries/nonexistent")
        assert resp.status_code == 404

    def test_advance_artifact_state(self):
        # Create
        payload = {
            "artifact_type": "model_artifact",
            "strategy_id": "api-test",
            "version": "1.0.0",
            "storage_ref": {"backend": "object_store", "path": "s3://bucket/art.bin"},
            "checksum": "sha256:xyz",
            "lineage": {"source_run_ids": ["run-001"]},
        }
        create_resp = self.client.post("/api/registry/entries", json=payload)
        registry_id = create_resp.json()["entry"]["registry_id"]

        # Draft -> Candidate
        resp = self.client.post(
            f"/api/registry/entries/{registry_id}/advance",
            json={"target_state": "candidate"},
        )
        assert resp.status_code == 200
        assert resp.json()["entry"]["artifact_state"] == "candidate"

        # Candidate -> Approved
        resp = self.client.post(
            f"/api/registry/entries/{registry_id}/advance",
            json={"target_state": "approved", "approver": "test-reviewer"},
        )
        assert resp.status_code == 200
        assert resp.json()["entry"]["artifact_state"] == "approved"
        assert resp.json()["entry"]["approver"] == "test-reviewer"

    def test_advance_forbidden(self):
        payload = {
            "artifact_type": "model_artifact",
            "strategy_id": "api-test",
            "version": "1.0.0",
            "storage_ref": {"backend": "object_store", "path": "s3://bucket/art.bin"},
            "checksum": "sha256:xyz",
        }
        create_resp = self.client.post("/api/registry/entries", json=payload)
        registry_id = create_resp.json()["entry"]["registry_id"]

        # Draft -> Approved (forbidden)
        resp = self.client.post(
            f"/api/registry/entries/{registry_id}/advance",
            json={"target_state": "approved"},
        )
        assert resp.status_code == 400
        assert "Forbidden" in resp.json()["detail"]

    def test_list_by_strategy(self):
        for v in ["1.0.0", "1.1.0", "2.0.0"]:
            self.client.post("/api/registry/entries", json={
                "artifact_type": "model_artifact",
                "strategy_id": "list-test",
                "version": v,
                "storage_ref": {"backend": "object_store", "path": "s3://bucket/art.bin"},
                "checksum": f"sha256:{v}",
            })

        resp = self.client.get("/api/registry/strategies/list-test/entries")
        assert resp.status_code == 200
        assert len(resp.json()) == 3

    def test_latest_approved(self):
        # Create and approve two versions
        for v in ["1.0.0", "2.0.0"]:
            create_resp = self.client.post("/api/registry/entries", json={
                "artifact_type": "model_artifact",
                "strategy_id": "latest-test",
                "version": v,
                "storage_ref": {"backend": "object_store", "path": "s3://bucket/art.bin"},
                "checksum": f"sha256:{v}",
                "lineage": {"source_run_ids": [f"run-{v}"]},
            })
            rid = create_resp.json()["entry"]["registry_id"]
            self.client.post(f"/api/registry/entries/{rid}/advance", json={"target_state": "candidate"})
            self.client.post(f"/api/registry/entries/{rid}/advance", json={"target_state": "approved"})

        resp = self.client.get("/api/registry/strategies/latest-test/latest-approved")
        assert resp.status_code == 200
        assert resp.json()["entry"]["version"] == "2.0.0"
        assert resp.json()["entry"]["artifact_state"] == "approved"

    def test_latest_approved_none(self):
        # Only draft, nothing approved
        self.client.post("/api/registry/entries", json={
            "artifact_type": "model_artifact",
            "strategy_id": "empty-test",
            "version": "1.0.0",
            "storage_ref": {"backend": "object_store", "path": "s3://bucket/art.bin"},
            "checksum": "sha256:xyz",
        })
        resp = self.client.get("/api/registry/strategies/empty-test/latest-approved")
        assert resp.status_code == 404

    def test_deployment_view(self):
        # Create and approve
        create_resp = self.client.post("/api/registry/entries", json={
            "artifact_type": "model_artifact",
            "strategy_id": "dv-test",
            "version": "1.0.0",
            "storage_ref": {"backend": "object_store", "path": "s3://bucket/art.bin"},
            "checksum": "sha256:xyz",
            "lineage": {"source_run_ids": ["run-001"]},
        })
        rid = create_resp.json()["entry"]["registry_id"]
        self.client.post(f"/api/registry/entries/{rid}/advance", json={"target_state": "candidate"})
        self.client.post(f"/api/registry/entries/{rid}/advance", json={"target_state": "approved"})

        # Update deployment summary (simulating deployment service)
        self.client.put(
            f"/api/registry/entries/{rid}/deployment-summary",
            json={
                "current_stage": "paper",
                "deployment_plan_id": "plan-001",
            },
        )

        # Check deployment view
        resp = self.client.get("/api/registry/strategies/dv-test/deployment-view")
        assert resp.status_code == 200
        data = resp.json()
        assert data["current_stage"] == "paper"
        assert data["deployment_plan_id"] == "plan-001"
        assert data["latest_approved_registry_id"] == rid

    def test_deployment_view_empty(self):
        resp = self.client.get("/api/registry/strategies/nonexistent/deployment-view")
        assert resp.status_code == 200
        data = resp.json()
        assert data["current_stage"] == "none"

    def test_api_reset_store_isolation(self):
        self.client.post("/api/registry/entries", json={
            "artifact_type": "model_artifact",
            "strategy_id": "isolation-test",
            "version": "1.0.0",
            "storage_ref": {"backend": "object_store", "path": "s3://bucket/art.bin"},
            "checksum": "sha256:xyz",
        })
        resp = self.client.get("/api/registry/strategies/isolation-test/entries")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

        reset_store()
        isolated_client = TestClient(app)
        resp = isolated_client.get("/api/registry/strategies/isolation-test/entries")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_split_semantics_approved_has_no_stage(self):
        """
        Key acceptance test: approving an artifact does NOT assign a deployment stage.
        The artifact is approved, deployment_stage remains none until deployment service acts.
        """
        create_resp = self.client.post("/api/registry/entries", json={
            "artifact_type": "model_artifact",
            "strategy_id": "split-test",
            "version": "1.0.0",
            "storage_ref": {"backend": "object_store", "path": "s3://bucket/art.bin"},
            "checksum": "sha256:xyz",
            "lineage": {"source_run_ids": ["run-001"]},
        })
        rid = create_resp.json()["entry"]["registry_id"]
        self.client.post(f"/api/registry/entries/{rid}/advance", json={"target_state": "candidate"})
        approved = self.client.post(
            f"/api/registry/entries/{rid}/advance",
            json={"target_state": "approved", "approver": "test"},
        )
        assert approved.status_code == 200
        # artifact_state is approved, deployment_stage is still none
        assert approved.json()["entry"]["artifact_state"] == "approved"
        assert approved.json()["deployment_stage"] == "none"

    def test_deployment_stage_updated_separately(self):
        """
        Deployment stage can be updated independently of artifact_state.
        This validates the split model: deployment_stage is not part of artifact lifecycle.
        """
        create_resp = self.client.post("/api/registry/entries", json={
            "artifact_type": "model_artifact",
            "strategy_id": "split-test",
            "version": "1.0.0",
            "storage_ref": {"backend": "object_store", "path": "s3://bucket/art.bin"},
            "checksum": "sha256:xyz",
            "lineage": {"source_run_ids": ["run-001"]},
        })
        rid = create_resp.json()["entry"]["registry_id"]
        self.client.post(f"/api/registry/entries/{rid}/advance", json={"target_state": "candidate"})
        self.client.post(f"/api/registry/entries/{rid}/advance", json={"target_state": "approved"})

        # Now update deployment_stage to paper (simulating deployment service)
        resp = self.client.put(
            f"/api/registry/entries/{rid}/deployment-summary",
            json={"current_stage": "paper", "deployment_plan_id": "plan-001"},
        )
        assert resp.status_code == 200
        assert resp.json()["entry"]["artifact_state"] == "approved"
        assert resp.json()["deployment_stage"] == "paper"

        # Advance artifact_state to retired — deployment_stage should remain
        self.client.post(f"/api/registry/entries/{rid}/advance", json={"target_state": "retired"})
        resp = self.client.get(f"/api/registry/entries/{rid}")
        assert resp.json()["entry"]["artifact_state"] == "retired"
        # deployment_summary still shows paper (read-model projection persists)
        assert resp.json()["deployment_stage"] == "paper"

    def test_advance_missing_entry_returns_404(self):
        """advance_state() on a non-existent registry_id must return 404, not 400."""
        resp = self.client.post(
            "/api/registry/entries/nonexistent-reg-id/advance",
            json={"target_state": "candidate"},
        )
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_advance_forbidden_transition_returns_400(self):
        """advance_state() with a forbidden transition must return 400, not 404."""
        create_resp = self.client.post("/api/registry/entries", json={
            "artifact_type": "model_artifact",
            "strategy_id": "err-test",
            "version": "1.0.0",
            "storage_ref": {"backend": "object_store", "path": "s3://bucket/art.bin"},
            "checksum": "sha256:xyz",
        })
        rid = create_resp.json()["entry"]["registry_id"]
        # draft -> approved is not an allowed direct transition
        resp = self.client.post(
            f"/api/registry/entries/{rid}/advance",
            json={"target_state": "approved"},
        )
        assert resp.status_code == 400
        assert "forbidden" in resp.json()["detail"].lower()

    def test_update_deployment_summary_missing_entry_returns_404(self):
        """update_deployment_summary() on a non-existent registry_id must return 404."""
        resp = self.client.put(
            "/api/registry/entries/nonexistent-reg-id/deployment-summary",
            json={"current_stage": "paper"},
        )
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_update_deployment_summary_unapproved_returns_400(self):
        """update_deployment_summary() with non-none stage on unapproved artifact must return 400."""
        create_resp = self.client.post("/api/registry/entries", json={
            "artifact_type": "model_artifact",
            "strategy_id": "deploy-err-test",
            "version": "1.0.0",
            "storage_ref": {"backend": "object_store", "path": "s3://bucket/art.bin"},
            "checksum": "sha256:xyz",
        })
        rid = create_resp.json()["entry"]["registry_id"]
        # artifact is still in draft state — projecting paper stage must fail with 400
        resp = self.client.put(
            f"/api/registry/entries/{rid}/deployment-summary",
            json={"current_stage": "paper"},
        )
        assert resp.status_code == 400
        assert "not approved" in resp.json()["detail"].lower()
