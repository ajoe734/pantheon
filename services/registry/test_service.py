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

import time

import pytest
from services.governance.test_approval_authority import (advance_registry_http, advance_registry_unit, registry_unit_headers, configure_registry_unit_auth)
from fastapi import HTTPException
from fastapi.testclient import TestClient

from services.runtime_auth_inbound import encode_jwt_hs256

from .models import (
    ArtifactType,
    ArtifactState,
    DeploymentStage,
    DeploymentSummary,
    Lineage,
    RegistryEntryCreate,
    StorageBackend,
    StorageRef,
)
from .service import _require_production_auth_configuration, app
from .split_api import RegistryError, RegistryNotFoundError, RegistryService
from .storage import RegistryStore, reset_store
from .strategy_artifact import (
    BUILTIN_STRATEGY_ARTIFACT_PATHS,
    load_strategy_artifact_registration,
    mutate_strategy_artifact,
)


# -- Fixtures -------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_store(monkeypatch):
    configure_registry_unit_auth(monkeypatch)
    """Reset the singleton store before each test."""
    reset_store()
    yield
    reset_store()


def _make_create_payload(
    strategy_id: str = "test-alpha",
    version: str = "1.0.0",
    artifact_state: ArtifactState = ArtifactState.DRAFT,
    artifact_type: ArtifactType | str = ArtifactType.MODEL_ARTIFACT,
) -> RegistryEntryCreate:
    return RegistryEntryCreate(
        artifact_type=artifact_type,
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
        entry = svc.register(_make_create_payload(), "reg-001", actor={"id": "unit-operator", "tenant": "tenant-unit"})
        assert entry.entry.artifact_state == ArtifactState.DRAFT

        advanced = advance_registry_unit(svc, "reg-001", ArtifactState.CANDIDATE)
        assert advanced.entry.artifact_state == ArtifactState.CANDIDATE

    def test_candidate_to_approved(self):
        store = RegistryStore()
        svc = RegistryService(store)
        entry = svc.register(_make_create_payload(), "reg-001", actor={"id": "unit-operator", "tenant": "tenant-unit"})
        advance_registry_unit(svc, "reg-001", ArtifactState.CANDIDATE)
        advanced = advance_registry_unit(
            svc, "reg-001",
            ArtifactState.APPROVED,
            approver="risk-committee",
            approval_decision_id="decision-reg-001",
        )
        assert advanced.entry.artifact_state == ArtifactState.APPROVED
        assert advanced.entry.approver == "risk-committee"
        assert advanced.entry.approval_decision_id == "decision-reg-001"
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
        actor={"id": "unit-operator", "tenant": "tenant-unit"})
        advance_registry_unit(svc, "reg-001", ArtifactState.CANDIDATE)
        with pytest.raises(RegistryError, match="Cannot approve artifact without lineage"):
            advance_registry_unit(svc, "reg-001", ArtifactState.APPROVED)

    def test_approved_to_retired(self):
        store = RegistryStore()
        svc = RegistryService(store)
        entry = svc.register(_make_create_payload(), "reg-001", actor={"id": "unit-operator", "tenant": "tenant-unit"})
        advance_registry_unit(svc, "reg-001", ArtifactState.CANDIDATE)
        advance_registry_unit(svc, "reg-001", ArtifactState.APPROVED)
        advanced = advance_registry_unit(svc, "reg-001", ArtifactState.RETIRED)
        assert advanced.entry.artifact_state == ArtifactState.RETIRED

    def test_draft_to_retired(self):
        store = RegistryStore()
        svc = RegistryService(store)
        svc.register(_make_create_payload(), "reg-001", actor={"id": "unit-operator", "tenant": "tenant-unit"})
        advanced = advance_registry_unit(svc, "reg-001", ArtifactState.RETIRED)
        assert advanced.entry.artifact_state == ArtifactState.RETIRED

    def test_candidate_to_retired(self):
        store = RegistryStore()
        svc = RegistryService(store)
        entry = svc.register(_make_create_payload(), "reg-001", actor={"id": "unit-operator", "tenant": "tenant-unit"})
        advance_registry_unit(svc, "reg-001", ArtifactState.CANDIDATE)
        advanced = advance_registry_unit(svc, "reg-001", ArtifactState.RETIRED)
        assert advanced.entry.artifact_state == ArtifactState.RETIRED

    def test_forbidden_transition_draft_to_approved(self):
        store = RegistryStore()
        svc = RegistryService(store)
        svc.register(_make_create_payload(), "reg-001", actor={"id": "unit-operator", "tenant": "tenant-unit"})
        with pytest.raises(RegistryError, match="Forbidden"):
            advance_registry_unit(svc, "reg-001", ArtifactState.APPROVED)

    def test_forbidden_transition_draft_to_retired_via_candidate(self):
        """Draft can go directly to retired, but not to approved without candidate."""
        store = RegistryStore()
        svc = RegistryService(store)
        svc.register(_make_create_payload(), "reg-001", actor={"id": "unit-operator", "tenant": "tenant-unit"})
        # Direct draft->retired IS allowed, so let's test retired->draft (impossible)
        advance_registry_unit(svc, "reg-001", ArtifactState.RETIRED)
        with pytest.raises(RegistryError, match="Forbidden"):
            advance_registry_unit(svc, "reg-001", ArtifactState.DRAFT)

    def test_forbidden_transition_retired_to_anything(self):
        store = RegistryStore()
        svc = RegistryService(store)
        svc.register(_make_create_payload(), "reg-001", actor={"id": "unit-operator", "tenant": "tenant-unit"})
        advance_registry_unit(svc, "reg-001", ArtifactState.RETIRED)
        for target in [ArtifactState.DRAFT, ArtifactState.CANDIDATE, ArtifactState.APPROVED]:
            with pytest.raises(RegistryError, match="Forbidden"):
                advance_registry_unit(svc, "reg-001", target)

    def test_approved_does_not_change_deployment_stage(self):
        """Approving an artifact does NOT set deployment_stage."""
        store = RegistryStore()
        svc = RegistryService(store)
        svc.register(_make_create_payload(), "reg-001", actor={"id": "unit-operator", "tenant": "tenant-unit"})
        advance_registry_unit(svc, "reg-001", ArtifactState.CANDIDATE)
        approved = advance_registry_unit(svc, "reg-001", ArtifactState.APPROVED)
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
            entry = store.get(rid)
            entry.artifact_state = ArtifactState.APPROVED
            store.update(entry)

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
        svc.register(_make_create_payload(), "reg-001", actor={"id": "unit-operator", "tenant": "tenant-unit"})
        advance_registry_unit(svc, "reg-001", ArtifactState.CANDIDATE)
        advance_registry_unit(svc, "reg-001", ArtifactState.APPROVED)

        view = svc.resolve_deployment_view("test-alpha")
        assert view.current_stage == DeploymentStage.NONE
        assert view.latest_approved_registry_id == "reg-001"

    def test_approved_with_deployment_summary(self):
        store = RegistryStore()
        svc = RegistryService(store)
        svc.register(_make_create_payload(), "reg-001", actor={"id": "unit-operator", "tenant": "tenant-unit"})
        advance_registry_unit(svc, "reg-001", ArtifactState.CANDIDATE)
        advance_registry_unit(svc, "reg-001", ArtifactState.APPROVED)

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
        svc.register(_make_create_payload(), "reg-001", actor={"id": "unit-operator", "tenant": "tenant-unit"})
        with pytest.raises(
            RegistryError,
            match="Cannot project a non-'none' deployment stage onto an artifact that is not approved",
        ):
            svc.update_deployment_summary("reg-001", current_stage=DeploymentStage.PAPER)


# -- FastAPI integration tests --------------------------------------------

class TestFastAPIEndpoints:
    def setup_method(self):
        self.client = TestClient(app, headers=registry_unit_headers())

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

    def test_register_behavior_policy_artifact_type(self):
        payload = {
            "artifact_type": "behavior_policy",
            "strategy_id": "imitation-alpha",
            "version": "0.1.0",
            "storage_ref": {
                "backend": "object_store",
                "path": "learning/imitation/imitation-alpha/0.1.0/artifact_bundle.json",
            },
            "checksum": "sha256:behavior-policy",
            "lineage": {
                "source_run_ids": ["imit-run-001"],
                "source_dataset_refs": ["dataset://imitation/imitation-alpha/0.1.0"],
            },
            "metadata": {
                "model_family": "imitation_policy",
                "algorithm": "behavior_cloning",
                "direct_live_influence": False,
            },
        }

        create_resp = self.client.post("/api/registry/entries", json=payload)

        assert create_resp.status_code == 200, create_resp.text
        registry_id = create_resp.json()["entry"]["registry_id"]
        assert create_resp.json()["entry"]["artifact_type"] == "behavior_policy"
        assert create_resp.json()["entry"]["artifact_state"] == "draft"
        assert create_resp.json()["deployment_stage"] == "none"

        candidate_resp = advance_registry_http(
            self.client, f"/api/registry/entries/{registry_id}/advance",
            json={"target_state": "candidate", "expected_artifact_state": "draft"},
        )
        assert candidate_resp.status_code == 200, candidate_resp.text

        approved_resp = advance_registry_http(
            self.client, f"/api/registry/entries/{registry_id}/advance",
            json={
                "target_state": "approved",
                "approver": "offline-eval-gate",
                "expected_artifact_state": "candidate",
            },
        )
        assert approved_resp.status_code == 200, approved_resp.text
        assert approved_resp.json()["entry"]["artifact_type"] == "behavior_policy"
        assert approved_resp.json()["entry"]["artifact_state"] == "approved"
        assert approved_resp.json()["deployment_stage"] == "none"

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

    def test_strategy_spec_same_id_registration_never_overwrites_approval(self):
        registry_id = "reg-strategy-spec-approved-race"
        payload = {
            "registry_id": registry_id,
            "strategy_id": "strat-approved-race",
            "version": "1.0.0",
            "source_seed_id": "source-seed-approved-race",
            "strategy_spec": _minimal_strategy_spec("strat-approved-race"),
        }
        created = self.client.post("/api/registry/strategy-specs", json=payload)
        assert created.status_code == 200, created.text
        current_state = "draft"
        for target_state in ("candidate", "approved"):
            advanced = advance_registry_http(
                self.client, f"/api/registry/strategy-specs/{registry_id}/advance",
                json={
                    "target_state": target_state,
                    "expected_artifact_state": current_state,
                },
            )
            assert advanced.status_code == 200, advanced.text
            current_state = target_state

        duplicate = self.client.post(
            "/api/registry/strategy-specs",
            json=payload,
        )

        assert duplicate.status_code == 200, duplicate.text
        # NOTE: per the gen-10 review's finding 4 (see
        # _ensure_strategy_spec_registration_matches's docstring in
        # service.py), a same-identity create replay's *response* now
        # reports the original creation snapshot (state "draft" here), not
        # the live current view — a caller retrying its own create command
        # gets back exactly what that command committed. This test's real
        # intent — that the duplicate registration never overwrites/reverts
        # the durable approval — is what the readback below actually proves.
        assert duplicate.json()["entry"]["artifact_state"] == "draft"
        readback = self.client.get(
            f"/api/registry/strategy-specs/{registry_id}"
        )
        assert readback.status_code == 200, readback.text
        entry = readback.json()["entry"]
        assert entry["artifact_state"] == "approved"
        assert (
            entry["metadata"]["strategy_spec"]["title"]
            == "Distilled momentum hypothesis"
        )

    def test_strategy_spec_same_id_registration_rejects_content_collision(self):
        registry_id = "reg-strategy-spec-content-collision"
        payload = {
            "registry_id": registry_id,
            "strategy_id": "strat-content-collision",
            "version": "1.0.0",
            "source_seed_id": "source-seed-original",
            "strategy_spec": _minimal_strategy_spec("strat-content-collision"),
        }
        created = self.client.post("/api/registry/strategy-specs", json=payload)
        assert created.status_code == 200, created.text

        duplicate = self.client.post(
            "/api/registry/strategy-specs",
            json={
                **payload,
                "source_seed_id": "source-seed-conflicting",
                "strategy_spec": {
                    **payload["strategy_spec"],
                    "title": "A duplicate create must not hide a collision",
                },
            },
        )

        assert duplicate.status_code == 400, duplicate.text
        assert "different content" in duplicate.json()["detail"]
        readback = self.client.get(
            f"/api/registry/strategy-specs/{registry_id}"
        )
        assert readback.status_code == 200, readback.text
        entry = readback.json()["entry"]
        assert entry["metadata"]["source_seed_id"] == "source-seed-original"
        assert (
            entry["metadata"]["strategy_spec"]["title"]
            == "Distilled momentum hypothesis"
        )

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

        advance_resp = advance_registry_http(
            self.client, f"/api/registry/strategy-specs/{registry_id}/advance",
            json={"target_state": "candidate", "expected_artifact_state": "draft"},
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
            "lineage": {"source_run_ids": ["run-get-entry"]},
        }
        create_resp = self.client.post("/api/registry/entries", json=payload)
        assert create_resp.status_code == 200, create_resp.text
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
        resp = advance_registry_http(
            self.client, f"/api/registry/entries/{registry_id}/advance",
            json={"target_state": "candidate", "expected_artifact_state": "draft"},
        )
        assert resp.status_code == 200
        assert resp.json()["entry"]["artifact_state"] == "candidate"

        # Candidate -> Approved
        resp = advance_registry_http(
            self.client, f"/api/registry/entries/{registry_id}/advance",
            json={
                "target_state": "approved",
                "approver": "test-reviewer",
                "approval_decision_id": "decision-api-test",
                "expected_artifact_state": "candidate",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["entry"]["artifact_state"] == "approved"
        assert resp.json()["entry"]["approver"] == "test-reviewer"
        assert resp.json()["entry"]["approval_decision_id"] == "decision-api-test"

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
        resp = advance_registry_http(
            self.client, f"/api/registry/entries/{registry_id}/advance",
            json={"target_state": "approved", "expected_artifact_state": "draft"},
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
            advance_registry_http(
                self.client, f"/api/registry/entries/{rid}/advance",
                json={"target_state": "candidate", "expected_artifact_state": "draft"},
            )
            advance_registry_http(
                self.client, f"/api/registry/entries/{rid}/advance",
                json={"target_state": "approved", "expected_artifact_state": "candidate"},
            )

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
        advance_registry_http(
            self.client, f"/api/registry/entries/{rid}/advance",
            json={"target_state": "candidate", "expected_artifact_state": "draft"},
        )
        advance_registry_http(
            self.client, f"/api/registry/entries/{rid}/advance",
            json={"target_state": "approved", "expected_artifact_state": "candidate"},
        )

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
        isolated_client = TestClient(app, headers=registry_unit_headers())
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
        advance_registry_http(
            self.client, f"/api/registry/entries/{rid}/advance",
            json={"target_state": "candidate", "expected_artifact_state": "draft"},
        )
        approved = advance_registry_http(
            self.client, f"/api/registry/entries/{rid}/advance",
            json={
                "target_state": "approved",
                "approver": "test",
                "expected_artifact_state": "candidate",
            },
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
        advance_registry_http(
            self.client, f"/api/registry/entries/{rid}/advance",
            json={"target_state": "candidate", "expected_artifact_state": "draft"},
        )
        advance_registry_http(
            self.client, f"/api/registry/entries/{rid}/advance",
            json={"target_state": "approved", "expected_artifact_state": "candidate"},
        )

        # Now update deployment_stage to paper (simulating deployment service)
        resp = self.client.put(
            f"/api/registry/entries/{rid}/deployment-summary",
            json={"current_stage": "paper", "deployment_plan_id": "plan-001"},
        )
        assert resp.status_code == 200
        assert resp.json()["entry"]["artifact_state"] == "approved"
        assert resp.json()["deployment_stage"] == "paper"

        # Advance artifact_state to retired — deployment_stage should remain
        advance_registry_http(
            self.client, f"/api/registry/entries/{rid}/advance",
            json={"target_state": "retired", "expected_artifact_state": "approved"},
        )
        resp = self.client.get(f"/api/registry/entries/{rid}")
        assert resp.json()["entry"]["artifact_state"] == "retired"
        # deployment_summary still shows paper (read-model projection persists)
        assert resp.json()["deployment_stage"] == "paper"

    def test_advance_missing_entry_returns_404(self):
        """advance_state() on a non-existent registry_id must return 404, not 400."""
        resp = advance_registry_http(
            self.client, "/api/registry/entries/nonexistent-reg-id/advance",
            json={"target_state": "candidate", "expected_artifact_state": "draft"},
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
        resp = advance_registry_http(
            self.client, f"/api/registry/entries/{rid}/advance",
            json={"target_state": "approved", "expected_artifact_state": "draft"},
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


# ===========================================================================
# Regression proofs for PR #5620 generation-4 rejection findings
# (REGISTRY-STRATEGY-UNIFIED-CONTRACT-001) — reproduced against the
# in-memory backend so they run without a live database. Each test would
# have failed against the pre-fix code and passes now.
#
# Postgres-specific proofs (real concurrent unique_fields collision, real
# fail-closed auth-config gate against a live durable backend) are gated on
# TEST_DATABASE_URL in test_owner_durability.py /
# services/foundation/tests/test_registry_owner_transaction.py, consistent
# with the rest of this package's Postgres-backed suites.
# ===========================================================================

_JWT_SECRET = "unified-contract-fixes-secret"


def _jwt(*, subject: str, tenant: str, roles=("operator",), **overrides) -> str:
    claims = {
        "sub": subject,
        "tenant": tenant,
        "roles": list(roles),
        "exp": time.time() + 3600,
    }
    claims.update(overrides)
    return encode_jwt_hs256(claims, secret=_JWT_SECRET)


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def strict_client(monkeypatch):
    monkeypatch.setenv("PANTHEON_REGISTRY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_REGISTRY_JWT_SECRET", _JWT_SECRET)
    monkeypatch.setenv("PANTHEON_REGISTRY_JWT_ISSUER", "")
    monkeypatch.setenv("PANTHEON_REGISTRY_JWT_AUDIENCE", "")
    return TestClient(app)


def _create_entry(client: TestClient, token: str, *, strategy_id: str, version: str = "1.0.0") -> dict:
    resp = client.post(
        "/api/registry/entries",
        json={
            "artifact_type": "model_artifact",
            "strategy_id": strategy_id,
            "version": version,
            "storage_ref": {"backend": "object_store", "path": "s3://bucket/a.bin"},
            "checksum": "sha256:deadbeef",
            "lineage": {"source_run_ids": ["run-1"]},
        },
        headers=_bearer(token),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _valid_spec(strategy_id: str, **variant_metadata) -> dict:
    """Minimal schema-valid StrategySpec (services/control-plane/specs/
    strategy_spec.schema.json) — reviewer finding 2 requires
    POST /api/registry/strategy-specs to enforce this schema, so tests
    exercising other behaviors (revision-identity, version-sequence) must
    submit schema-complete payloads. ``variant_metadata`` lands under the
    schema's open ``metadata`` object to distinguish otherwise-identical
    payload variants without violating the root's ``additionalProperties: false``.
    """
    spec = {
        "spec_version": "1.0",
        "strategy_id": strategy_id,
        "title": "Registry service probe strategy",
        "hypothesis": "Deterministic probe hypothesis for registry service tests.",
        "objective": "Prove real registry write-owner capability, not just route existence.",
        "market_scope": {"symbols": ["TEST"], "frequency": "1d"},
        "data_dependencies": [{"ref": "test-fixture", "kind": "note"}],
        "execution_profile": {"signal_schema_version": "1.0", "quantity_type": "SHARES"},
        "evaluation_plan": {"metrics": ["sharpe"]},
        "governance": {"approval_required": True},
        "provenance": {"source_kind": "manual", "created_at": "2026-01-01T00:00:00Z"},
    }
    if variant_metadata:
        spec["metadata"] = dict(variant_metadata)
    return spec


# -- Finding 1: target authorization / system scope -----------------------


def test_cross_tenant_patch_is_denied_not_200(strict_client):
    """A verified tenant-B caller must not be able to PATCH tenant-A's entry
    (previously 200 with last_actor silently reassigned to B)."""
    owner_token = _jwt(subject="owner-1", tenant="tenant-a")
    other_token = _jwt(subject="intruder-1", tenant="tenant-b")

    created = _create_entry(strict_client, owner_token, strategy_id="cross-tenant-strat")
    registry_id = created["entry"]["registry_id"]

    resp = strict_client.patch(
        f"/api/registry/entries/{registry_id}/metadata",
        json={"expected_metadata": None, "metadata": {"note": "hijacked"}},
        headers=_bearer(other_token),
    )
    assert resp.status_code == 403, resp.text

    # The entry's last_actor must not have been reassigned to the intruder.
    unchanged = strict_client.get(f"/api/registry/entries/{registry_id}", headers=_bearer(owner_token))
    assert unchanged.json()["entry"]["last_actor"]["actor_id"] == "owner-1"


def test_anonymous_get_is_denied(strict_client):
    owner_token = _jwt(subject="owner-2", tenant="tenant-a")
    created = _create_entry(strict_client, owner_token, strategy_id="anon-get-strat")
    registry_id = created["entry"]["registry_id"]

    resp = strict_client.get(f"/api/registry/entries/{registry_id}")
    assert resp.status_code == 401, resp.text


def test_cross_tenant_get_is_denied(strict_client):
    owner_token = _jwt(subject="owner-3", tenant="tenant-a")
    other_token = _jwt(subject="reader-1", tenant="tenant-b")
    created = _create_entry(strict_client, owner_token, strategy_id="cross-tenant-get-strat")
    registry_id = created["entry"]["registry_id"]

    resp = strict_client.get(f"/api/registry/entries/{registry_id}", headers=_bearer(other_token))
    assert resp.status_code == 403, resp.text


def test_same_tenant_read_and_write_still_succeed(strict_client):
    """The scoping fix must not become a blanket deny — same-tenant access
    (including a second caller within that tenant) still works."""
    token_a = _jwt(subject="owner-4", tenant="tenant-a")
    token_a2 = _jwt(subject="colleague-4", tenant="tenant-a")
    created = _create_entry(strict_client, token_a, strategy_id="same-tenant-strat")
    registry_id = created["entry"]["registry_id"]

    resp = strict_client.get(f"/api/registry/entries/{registry_id}", headers=_bearer(token_a2))
    assert resp.status_code == 200, resp.text

    patched = strict_client.patch(
        f"/api/registry/entries/{registry_id}/metadata",
        json={"expected_metadata": None, "metadata": {"note": "same tenant ok"}},
        headers=_bearer(token_a2),
    )
    assert patched.status_code == 200, patched.text


def test_builtin_artifact_mutation_is_denied_and_survives_restart(strict_client):
    """A builtin StrategyArtifact must reject any caller PATCH (even an
    unchanged reserved payload plus a harmless extra note), and re-running
    the bootstrap idempotent registration must still succeed cleanly
    afterwards (restart invariant)."""
    registry_id = "artifact-tw-session-momentum-v1"
    token = _jwt(subject="operator-x", tenant="tenant-a")

    resp = strict_client.patch(
        f"/api/registry/entries/{registry_id}/metadata",
        json={"expected_metadata": None, "metadata": {"note": "should not apply"}},
        headers=_bearer(token),
    )
    assert resp.status_code == 403, resp.text

    # Restart invariant: a fresh service/store must still register builtins
    # cleanly (the denied PATCH above must not have partially mutated it).
    reset_store()
    fresh = TestClient(app)
    health = fresh.get("/health")
    assert health.status_code == 200, health.text
    readback = fresh.get(
        f"/api/registry/strategy-artifacts/{registry_id}", headers=_bearer(token)
    )
    assert readback.status_code == 200, readback.text


def test_builtin_artifact_is_publicly_readable_across_tenants(strict_client):
    """Builtins remain readable reference data for any verified caller —
    the scoping fix must not accidentally lock them to one tenant."""
    registry_id = "artifact-tw-session-momentum-v1"
    token = _jwt(subject="any-reader", tenant="some-other-tenant")
    resp = strict_client.get(
        f"/api/registry/strategy-artifacts/{registry_id}", headers=_bearer(token)
    )
    assert resp.status_code == 200, resp.text


def test_caller_cannot_assert_reserved_builtin_tenant_claim(strict_client):
    forged_token = _jwt(subject="forger", tenant="__builtin__")
    resp = strict_client.post(
        "/api/registry/entries",
        json={
            "artifact_type": "model_artifact",
            "strategy_id": "forged-tenant-strat",
            "version": "1.0.0",
            "storage_ref": {"backend": "object_store", "path": "s3://bucket/a.bin"},
            "checksum": "sha256:deadbeef",
        },
        headers=_bearer(forged_token),
    )
    assert resp.status_code == 403, resp.text


# -- Finding 2: fail-closed configuration ----------------------------------


def test_production_auth_config_required_once_postgres_backend_selected(monkeypatch):
    """Once the durable backend is selected, permissive/unconfigured auth
    must fail closed (500) rather than silently accepting an unsigned
    structured Bearer token or a strict token with no expected issuer/
    audience configured."""
    monkeypatch.setattr("services.registry.pg_store._registry_backend", lambda: "postgres")

    # Permissive mode (or unset) with the durable backend selected: reject.
    with pytest.raises(HTTPException) as excinfo:
        _require_production_auth_configuration({"PANTHEON_RUNTIME_AUTH_MODE": "permissive"})
    assert excinfo.value.status_code == 500

    # Strict mode declared but no issuer/audience configured: still reject —
    # a signed token asserting *any* issuer/audience would otherwise pass.
    with pytest.raises(HTTPException) as excinfo:
        _require_production_auth_configuration({
            "PANTHEON_RUNTIME_AUTH_MODE": "strict",
            "PANTHEON_RUNTIME_JWT_ISSUER": "",
            "PANTHEON_RUNTIME_JWT_AUDIENCE": "",
        })
    assert excinfo.value.status_code == 500

    # Fully configured: passes through without raising.
    _require_production_auth_configuration({
        "PANTHEON_RUNTIME_AUTH_MODE": "strict",
        "PANTHEON_RUNTIME_JWT_ISSUER": "iss",
        "PANTHEON_RUNTIME_JWT_AUDIENCE": "aud",
    })


def test_memory_backend_is_unaffected_by_production_auth_gate(monkeypatch):
    """The in-memory test double is explicitly exempt — this is what keeps
    the rest of this package's unit tests running without strict JWT setup."""
    monkeypatch.setattr("services.registry.pg_store._registry_backend", lambda: "memory")
    _require_production_auth_configuration({"PANTHEON_RUNTIME_AUTH_MODE": "permissive"})


def test_dsn_configured_without_explicit_postgres_backend_fails_closed(monkeypatch):
    """architecture-resumption-sa-sd.md §3.1: a configured DSN with an unset/
    memory backend must not silently select the in-memory store — that would
    look like a working deployment while every write vanished on exit."""
    from .storage import build_registry_store

    monkeypatch.delenv("REGISTRY_STORE_BACKEND", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://example/registry")
    monkeypatch.delenv("PANTHEON_ENV", raising=False)
    monkeypatch.delenv("PANTHEON_PERSISTENCE_POSTURE", raising=False)
    with pytest.raises(RuntimeError):
        build_registry_store()


# -- Finding 3: genuine positive capabilities ------------------------------


def test_name_only_draft_creation_succeeds(strict_client):
    token = _jwt(subject="drafter", tenant="tenant-a")
    resp = strict_client.post(
        "/api/registry/entries",
        json={"name": "My Draft Strategy Idea"},
        headers=_bearer(token),
    )
    assert resp.status_code == 200, resp.text
    entry = resp.json()["entry"]
    assert entry["metadata"]["name"] == "My Draft Strategy Idea"
    assert entry["artifact_state"] == "draft"
    assert entry["strategy_id"]  # stable synthesized identity assigned

    # Stable identity: fetching it back returns the same registry_id/content.
    readback = strict_client.get(
        f"/api/registry/entries/{entry['registry_id']}", headers=_bearer(token)
    )
    assert readback.status_code == 200, readback.text
    assert readback.json()["entry"]["strategy_id"] == entry["strategy_id"]


def test_mixed_name_and_partial_typed_fields_is_rejected(strict_client):
    token = _jwt(subject="drafter-2", tenant="tenant-a")
    resp = strict_client.post(
        "/api/registry/entries",
        json={"name": "Mixed", "strategy_id": "should-not-mix"},
        headers=_bearer(token),
    )
    assert resp.status_code == 400, resp.text


def test_neither_name_nor_full_fields_is_rejected(strict_client):
    token = _jwt(subject="drafter-3", tenant="tenant-a")
    resp = strict_client.post("/api/registry/entries", json={}, headers=_bearer(token))
    assert resp.status_code == 400, resp.text


def test_typed_submission_cannot_mix_name(strict_client):
    token = _jwt(subject="drafter-4", tenant="tenant-a")
    resp = strict_client.post(
        "/api/registry/entries",
        json={
            "name": "Mixed Full",
            "artifact_type": "strategy_spec",
            "strategy_id": "test-strat",
            "version": "1.0.0",
        },
        headers=_bearer(token),
    )
    assert resp.status_code == 400, resp.text
    assert "mix" in resp.json()["detail"].lower()


def test_typed_submission_rejects_reserved_draft_kind(strict_client):
    token = _jwt(subject="drafter-5", tenant="tenant-a")
    resp = strict_client.post(
        "/api/registry/entries",
        json={
            "artifact_type": "strategy_spec",
            "strategy_id": "test-strat",
            "version": "1.0.0",
            "metadata": {"draft_kind": "name_only"},
        },
        headers=_bearer(token),
    )
    assert resp.status_code == 400, resp.text
    assert "draft_kind" in resp.json()["detail"].lower()


def test_strategy_spec_facade_rejects_reserved_draft_kind(strict_client):
    token = _jwt(subject="drafter-6", tenant="tenant-a")
    resp = strict_client.post(
        "/api/registry/strategy-specs",
        json={
            "strategy_id": "test-strat-facade",
            "version": "1.0.0",
            "lineage": {"source_run_ids": ["run-1"]},
            "strategy_spec": _valid_spec("test-strat-facade"),
            "metadata": {"draft_kind": "name_only"},
        },
        headers=_bearer(token),
    )
    assert resp.status_code == 400, resp.text
    assert "draft_kind" in resp.json()["detail"].lower()


def test_metadata_patch_cannot_introduce_reserved_key_that_never_existed(strict_client):
    """A generic metadata PATCH must not be able to smuggle in a fresh
    strategy_spec (or other reserved key) on an entry that never had one —
    that would bypass the dedicated schema/checksum-validated registration
    path entirely."""
    token = _jwt(subject="smuggler", tenant="tenant-a")
    created = _create_entry(strict_client, token, strategy_id="smuggle-strat")
    registry_id = created["entry"]["registry_id"]
    assert created["entry"]["metadata"] is None

    resp = strict_client.patch(
        f"/api/registry/entries/{registry_id}/metadata",
        json={
            "expected_metadata": None,
            "metadata": {"strategy_spec": {"strategy_id": "smuggle-strat", "spec_version": "1.0"}},
        },
        headers=_bearer(token),
    )
    assert resp.status_code == 409, resp.text


# -- Finding 4: immutable revision identity --------------------------------


def test_two_registry_ids_cannot_both_win_the_same_strategy_version(strict_client):
    """register_if_absent must enforce a real unique (strategy_id, version,
    artifact_type) tuple — two different caller-supplied registry_ids at the
    same version must not both succeed with divergent content."""
    token = _jwt(subject="revision-writer", tenant="tenant-a")
    strategy_id = "revision-identity-strat"
    lineage = {"source_run_ids": ["run-1"]}

    first = strict_client.post(
        "/api/registry/strategy-specs",
        json={
            "strategy_id": strategy_id,
            "version": "1.0.0",
            "lineage": lineage,
            "strategy_spec": _valid_spec(strategy_id),
        },
        headers=_bearer(token),
    )
    assert first.status_code == 200, first.text

    second = strict_client.post(
        "/api/registry/strategy-specs",
        json={
            "strategy_id": strategy_id,
            "version": "1.0.0",
            "registry_id": "reg-a-different-supplied-id",
            "lineage": lineage,
            "strategy_spec": _valid_spec(strategy_id, diverged=True),
        },
        headers=_bearer(token),
    )
    assert second.status_code == 409, second.text

    listed = strict_client.get(
        f"/api/registry/strategies/{strategy_id}/entries", headers=_bearer(token)
    )
    versions_at_1_0_0 = [
        e["entry"]["registry_id"] for e in listed.json() if e["entry"]["version"] == "1.0.0"
    ]
    assert len(versions_at_1_0_0) == 1


def test_parent_linked_revision_cannot_downgrade_version(strict_client):
    token = _jwt(subject="downgrade-writer", tenant="tenant-a")
    strategy_id = "downgrade-strat"
    base = strict_client.post(
        "/api/registry/strategy-specs",
        json={
            "strategy_id": strategy_id,
            "version": "1.0.0",
            "lineage": {"source_run_ids": ["run-1"]},
            "strategy_spec": _valid_spec(strategy_id),
        },
        headers=_bearer(token),
    )
    assert base.status_code == 200, base.text
    parent_id = base.json()["entry"]["registry_id"]

    downgrade = strict_client.post(
        "/api/registry/strategy-specs",
        json={
            "strategy_id": strategy_id,
            "version": "0.0.1",
            "lineage": {"source_run_ids": ["run-1"], "parent_registry_ids": [parent_id]},
            "strategy_spec": _valid_spec(strategy_id, v=2),
        },
        headers=_bearer(token),
    )
    assert downgrade.status_code == 400, downgrade.text
    assert "greater" in downgrade.json()["detail"].lower()


def test_register_if_absent_unique_fields_enforced_at_store_layer():
    """Direct store-level proof (no HTTP), matching the atomic
    create_if_absent contract used by register_if_absent."""
    from .models import ArtifactType, Lineage, RegistryEntryCreate, StorageBackend, StorageRef

    service = RegistryService(RegistryStore())
    payload = RegistryEntryCreate(
        artifact_type=ArtifactType.STRATEGY_SPEC,
        strategy_id="direct-strat",
        version="1.0.0",
        lineage=Lineage(source_run_ids=["run-1"]),
        storage_ref=StorageRef(backend=StorageBackend.INLINE, path="x"),
        checksum="sha256:aaa",
    )
    view1, created1 = service.register_if_absent(payload, "reg-one")
    assert created1 is True

    view2, created2 = service.register_if_absent(payload, "reg-two")
    assert created2 is False
    assert view2.entry.registry_id == view1.entry.registry_id == "reg-one"


# -- Finding 6: replay semantics -------------------------------------------


def test_replay_with_changed_precondition_is_not_treated_as_replay(strict_client):
    """A same-key request with a *changed* expected_metadata precondition
    but identical target metadata must not be silently accepted as
    replay=true — the whole precondition is part of the normalized request."""
    token = _jwt(subject="replay-writer", tenant="tenant-a")
    created = _create_entry(strict_client, token, strategy_id="replay-precondition-strat")
    registry_id = created["entry"]["registry_id"]

    first = strict_client.patch(
        f"/api/registry/entries/{registry_id}/metadata",
        json={"expected_metadata": None, "metadata": {"note": "same-target"}, "command_key": "cmd-1"},
        headers=_bearer(token),
    )
    assert first.status_code == 200, first.text

    # Same command_key, *different* claimed precondition, identical target
    # metadata as the (now-current) value — must be a divergent-replay
    # conflict (409), not a false "replay=true" no-op.
    second = strict_client.patch(
        f"/api/registry/entries/{registry_id}/metadata",
        json={
            "expected_metadata": {"note": "some-other-base"},
            "metadata": {"note": "same-target"},
            "command_key": "cmd-1",
        },
        headers=_bearer(token),
    )
    assert second.status_code == 409, second.text


def test_divergent_replay_maps_to_409_not_500(strict_client):
    token = _jwt(subject="divergent-writer", tenant="tenant-a")
    created = _create_entry(strict_client, token, strategy_id="divergent-replay-strat")
    registry_id = created["entry"]["registry_id"]

    first = strict_client.patch(
        f"/api/registry/entries/{registry_id}/metadata",
        json={"expected_metadata": None, "metadata": {"note": "v1"}, "command_key": "cmd-div-1"},
        headers=_bearer(token),
    )
    assert first.status_code == 200, first.text

    divergent = strict_client.patch(
        f"/api/registry/entries/{registry_id}/metadata",
        json={"expected_metadata": None, "metadata": {"note": "v2-different"}, "command_key": "cmd-div-1"},
        headers=_bearer(token),
    )
    assert divergent.status_code == 409, divergent.text


def test_receipt_key_does_not_collide_across_ambiguous_tenant_actor_boundary():
    """``tenant="a:b", actor="c"`` must not collide with ``tenant="a",
    actor="b:c"`` — the delimiter used to join the components must not be
    forgeable by either field's content."""
    from .pg_store import PostgresRegistryStore

    key_1 = PostgresRegistryStore.receipt_key(
        "cmd-1", "reg-1", actor={"tenant": "a:b", "actor_id": "c"}
    )
    key_2 = PostgresRegistryStore.receipt_key(
        "cmd-1", "reg-1", actor={"tenant": "a", "actor_id": "b:c"}
    )
    assert key_1 != key_2


# -- Gen-5 reviewer findings ------------------------------------------------


def test_readiness_dependency_reports_memory_backend_as_degraded_not_ok(monkeypatch):
    """Reviewer finding 8: readiness must not silently report ready=true
    with no dependency evidence regardless of the selected owner backend —
    the in-memory test double must be explicitly surfaced as degraded, not
    conflated with a reachable durable production owner.

    Reviewer finding 7 (gen-8 review): REGISTRY_STORE_BACKEND must be
    explicitly set to opt into the in-memory test double (see
    storage.build_registry_store) — this test injects it explicitly rather
    than relying on a since-removed unset-implies-memory default.
    """
    for key in ("REGISTRY_STORE_BACKEND", "PANTHEON_ENV", "PANTHEON_PERSISTENCE_POSTURE", "DATABASE_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("REGISTRY_STORE_BACKEND", "memory")
    reset_store()
    from . import main as registry_main

    dependency = registry_main._registry_owner_dependency()
    assert dependency["status"] == "degraded"
    assert dependency["backend"] == "memory"
    reset_store()


def test_latest_approved_endpoint_does_not_leak_another_tenants_approved_entry(strict_client):
    """Reviewer finding 1: the aggregate resolve must not surface another
    tenant's approved entry just because it is the semver-latest across all
    tenants."""
    strategy_id = "cross-tenant-latest"
    token_a = _jwt(subject="writer-a", tenant="tenant-a")
    token_b = _jwt(subject="writer-b", tenant="tenant-b")

    created_a = _create_entry(strict_client, token_a, strategy_id=strategy_id, version="1.0.0")
    registry_id_a = created_a["entry"]["registry_id"]
    advance_registry_http(
        strict_client, f"/api/registry/entries/{registry_id_a}/advance",
        json={"target_state": "candidate", "expected_artifact_state": "draft"},
        headers=_bearer(token_a),
    )
    advance_registry_http(
        strict_client, f"/api/registry/entries/{registry_id_a}/advance",
        json={"target_state": "approved", "expected_artifact_state": "candidate"},
        headers=_bearer(token_a),
    )

    # tenant-b has never registered anything for this strategy_id — the
    # aggregate resolve must report 404, not tenant-a's approved entry.
    resp = strict_client.get(
        f"/api/registry/strategies/{strategy_id}/latest-approved", headers=_bearer(token_b)
    )
    assert resp.status_code == 404, resp.text


def test_register_entry_idempotency_key_replays_original_identity(strict_client):
    """Reviewer finding 4: a retried identical name-only draft POST under the
    same Idempotency-Key must return the originally-created entry, not a
    freshly synthesized strategy identity."""
    token = _jwt(subject="idem-writer", tenant="tenant-a")
    headers = dict(_bearer(token))
    headers["Idempotency-Key"] = "draft-create-001"

    first = strict_client.post(
        "/api/registry/entries", json={"name": "Idempotent Draft"}, headers=headers,
    )
    assert first.status_code == 200, first.text
    first_entry = first.json()["entry"]

    second = strict_client.post(
        "/api/registry/entries", json={"name": "Idempotent Draft"}, headers=headers,
    )
    assert second.status_code == 200, second.text
    second_entry = second.json()["entry"]

    assert second_entry["registry_id"] == first_entry["registry_id"]
    assert second_entry["strategy_id"] == first_entry["strategy_id"]


def test_register_entry_without_idempotency_key_still_synthesizes_fresh_identity(strict_client):
    """No Idempotency-Key header means no idempotency contract at all —
    two calls remain two distinct drafts, preserving existing behavior."""
    token = _jwt(subject="idem-writer-2", tenant="tenant-a")
    first = strict_client.post(
        "/api/registry/entries", json={"name": "Plain Draft"}, headers=_bearer(token),
    )
    second = strict_client.post(
        "/api/registry/entries", json={"name": "Plain Draft"}, headers=_bearer(token),
    )
    assert first.json()["entry"]["strategy_id"] != second.json()["entry"]["strategy_id"]


def test_create_enforces_composite_unique_identity_not_just_registry_id(strict_client):
    """Reviewer finding 3: the plain register() create path (used by e.g.
    model_artifact entries) must also atomically reserve
    (strategy_id, version, artifact_type) — not only the caller-generated
    registry_id — so a second distinct registry_id cannot collide on the
    same revision identity."""
    from .storage import RegistryStore

    store = RegistryStore()
    svc = RegistryService(store)
    payload = _make_create_payload(strategy_id="unique-identity-strat", version="1.0.0")
    svc.register(payload, "reg-first", actor={"id": "unit-operator", "tenant": "tenant-unit"})
    with pytest.raises(RegistryError):
        svc.register(payload, "reg-second", actor={"id": "unit-operator", "tenant": "tenant-unit"})


# ===========================================================================
# Regression proofs for the generation-6 Codex rejection of PR #5620
# (REGISTRY-STRATEGY-UNIFIED-CONTRACT-001) — 9 findings reproduced against
# live isolated PostgreSQL. Each of the in-process-reproducible ones is
# pinned here against the in-memory backend; Postgres-specific proofs
# (TOCTOU races, advisory-lock bootstrap serialization) live in
# services/foundation/tests/test_registry_owner_transaction.py and
# services/registry/test_owner_durability_real_process.py.
# ===========================================================================


def test_whitespace_only_sub_claim_is_rejected_not_synthesized(strict_client):
    """Reviewer finding 1: a JWT with sub=" " (whitespace-only) must be
    rejected as malformed, not silently fall through to a synthesized
    actor_id="internal-api-operator" and get persisted."""
    token = _jwt(subject="ignored", tenant="tenant-a", sub=" ")
    resp = strict_client.post(
        "/api/registry/entries",
        json={"name": "whitespace-sub-draft"},
        headers=_bearer(token),
    )
    assert resp.status_code == 403, resp.text


def test_whitespace_only_tenant_claim_is_rejected_not_persisted_as_null(strict_client):
    """Reviewer finding 1: a JWT with tenant=" " (whitespace-only) must be
    rejected as malformed, not silently persisted as owner_tenant=null."""
    token = _jwt(subject="op-1", tenant=" ")
    resp = strict_client.post(
        "/api/registry/entries",
        json={"name": "whitespace-tenant-draft"},
        headers=_bearer(token),
    )
    assert resp.status_code == 403, resp.text


def test_whitespace_padded_builtin_tenant_claim_is_still_rejected(strict_client):
    """Reviewer finding 1: " __builtin__ " (whitespace-padded reserved
    marker) must still be caught by the reserved-tenant check — stripping
    happens before the comparison, not after."""
    token = _jwt(subject="forger-2", tenant=" __builtin__ ")
    resp = strict_client.post(
        "/api/registry/entries",
        json={"name": "padded-builtin-draft"},
        headers=_bearer(token),
    )
    assert resp.status_code == 403, resp.text


def test_generic_route_strategy_spec_with_embedded_spec_requires_lineage(strict_client):
    """Reviewer finding 2: a caller registering a *full* StrategySpec
    (embedded metadata.strategy_spec content) through the generic
    /api/registry/entries route must satisfy the same lineage requirement as
    the dedicated /strategy-specs facade — a bare checksum-only reference
    entry (no embedded content) is unaffected and still allowed with no
    lineage, but embedded content with empty lineage must be rejected."""
    token = _jwt(subject="generic-writer", tenant="tenant-a")
    strategy_id = "generic-route-strat"
    resp = strict_client.post(
        "/api/registry/entries",
        json={
            "artifact_type": "strategy_spec",
            "strategy_id": strategy_id,
            "version": "1.0.0",
            "metadata": {"strategy_spec": _valid_spec(strategy_id)},
        },
        headers=_bearer(token),
    )
    assert resp.status_code == 400, resp.text
    assert "lineage" in resp.json()["detail"].lower()


def test_generic_route_strategy_spec_with_embedded_spec_enforces_version_sequence(strict_client):
    """Reviewer finding 2: an out-of-sequence StrategySpec version (e.g.
    9.9.9 with no valid parent link) must be rejected through the generic
    /api/registry/entries route the same way the dedicated
    POST /api/registry/strategy-specs facade already rejects it — otherwise
    the dedicated route's rejection is trivially bypassable by posting
    identical content through the generic route instead."""
    token = _jwt(subject="generic-writer-2", tenant="tenant-a")
    strategy_id = "generic-route-sequence-strat"

    first = strict_client.post(
        "/api/registry/strategy-specs",
        json={
            "strategy_id": strategy_id,
            "version": "1.0.0",
            "lineage": {"source_run_ids": ["run-1"]},
            "strategy_spec": _valid_spec(strategy_id),
        },
        headers=_bearer(token),
    )
    assert first.status_code == 200, first.text

    jump = strict_client.post(
        "/api/registry/entries",
        json={
            "artifact_type": "strategy_spec",
            "strategy_id": strategy_id,
            "version": "9.9.9",
            "lineage": {"source_run_ids": ["run-1"]},
            "metadata": {"strategy_spec": _valid_spec(strategy_id, v=2)},
        },
        headers=_bearer(token),
    )
    assert jump.status_code == 400, jump.text
    assert "valid next revision" in jump.json()["detail"].lower()

    # The rejected jump must never have been persisted.
    listed = strict_client.get(
        f"/api/registry/strategies/{strategy_id}/entries", headers=_bearer(token),
    )
    versions = [e["entry"]["version"] for e in listed.json()]
    assert "9.9.9" not in versions


def test_dedicated_route_rejects_empty_metadata_strategy_spec_with_arbitrary_checksum(strict_client):
    """Reviewer finding 2: metadata.strategy_spec={} with no top-level
    strategy_spec and an arbitrary caller-supplied checksum must not be
    silently accepted as if it were a validated (or intentionally empty)
    spec — there is no actual content for that checksum to correspond to."""
    token = _jwt(subject="empty-spec-writer", tenant="tenant-a")
    resp = strict_client.post(
        "/api/registry/strategy-specs",
        json={
            "strategy_id": "empty-spec-strat",
            "version": "1.0.0",
            "lineage": {"source_run_ids": ["run-1"]},
            "checksum": "sha256:arbitrary-unvalidated-checksum",
            "metadata": {"strategy_spec": {}},
        },
        headers=_bearer(token),
    )
    assert resp.status_code == 400, resp.text


def test_idempotency_key_replay_with_divergent_request_is_409_not_silent_original(strict_client):
    """Reviewer finding 3: the same Idempotency-Key reused with a genuinely
    different request (a different draft name) must fail closed (409), not
    silently return the entry created by the *first* request as if it
    satisfied the second, different one."""
    token = _jwt(subject="idem-divergent-writer", tenant="tenant-a")
    headers = dict(_bearer(token))
    headers["Idempotency-Key"] = "divergent-create-001"

    first = strict_client.post(
        "/api/registry/entries", json={"name": "Alpha"}, headers=headers,
    )
    assert first.status_code == 200, first.text
    first_entry = first.json()["entry"]

    second = strict_client.post(
        "/api/registry/entries", json={"name": "Beta"}, headers=headers,
    )
    assert second.status_code == 409, second.text

    # The original entry must be unaffected by the divergent replay attempt.
    readback = strict_client.get(
        f"/api/registry/entries/{first_entry['registry_id']}", headers=_bearer(token),
    )
    assert readback.json()["entry"]["metadata"]["name"] == "Alpha"


def test_advance_command_key_replay_returns_original_receipt_not_forbidden_transition(strict_client):
    """Reviewer finding 5: a retried advance under the same command_key must
    return the entry exactly as originally committed, not re-run the
    transition (which would otherwise raise a spurious "forbidden
    transition" error once the entry has already moved past draft)."""
    token = _jwt(subject="advance-writer", tenant="tenant-a")
    created = _create_entry(strict_client, token, strategy_id="advance-receipt-strat")
    registry_id = created["entry"]["registry_id"]

    first = advance_registry_http(
        strict_client, f"/api/registry/entries/{registry_id}/advance",
        json={
            "target_state": "candidate",
            "command_key": "advance-cmd-001",
            "expected_artifact_state": "draft",
        },
        headers=_bearer(token),
    )
    assert first.status_code == 200, first.text
    assert first.json()["entry"]["artifact_state"] == "candidate"

    # A true replay resends the identical original request — including the
    # original base state ("draft"), not the post-transition state — since
    # it must be recognized as the same logical command and short-circuited
    # to the original receipt rather than re-evaluated against the entry's
    # (now advanced) current state.
    replay = advance_registry_http(
        strict_client, f"/api/registry/entries/{registry_id}/advance",
        json={
            "target_state": "candidate",
            "command_key": "advance-cmd-001",
            "expected_artifact_state": "draft",
        },
        headers=_bearer(token),
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["entry"]["artifact_state"] == "candidate"
    assert replay.json()["entry"]["registry_id"] == registry_id


def test_advance_command_key_divergent_replay_is_409(strict_client):
    """Reviewer finding 5: reusing an advance command_key with a genuinely
    different target_state must fail closed (409), not silently accept a
    second transition under the same key."""
    token = _jwt(subject="advance-writer-2", tenant="tenant-a")
    created = _create_entry(strict_client, token, strategy_id="advance-divergent-strat")
    registry_id = created["entry"]["registry_id"]

    first = advance_registry_http(
        strict_client, f"/api/registry/entries/{registry_id}/advance",
        json={
            "target_state": "candidate",
            "command_key": "advance-cmd-shared",
            "expected_artifact_state": "draft",
        },
        headers=_bearer(token),
    )
    assert first.status_code == 200, first.text

    # The entry has actually moved to "candidate" by this point; the
    # divergent-target-state-under-shared-command_key check must still fire
    # a 409 for the command_key mismatch, not a different "stale base" 409 —
    # so expected_artifact_state here reflects the entry's true current state.
    diverged = advance_registry_http(
        strict_client, f"/api/registry/entries/{registry_id}/advance",
        json={
            "target_state": "retired",
            "command_key": "advance-cmd-shared",
            "expected_artifact_state": "candidate",
        },
        headers=_bearer(token),
    )
    assert diverged.status_code == 409, diverged.text


# ===========================================================================
# Regression proofs for the gen-8 independent Codex rejection of PR #5620
# (exact head 6e0ed787803815cd36fff1a529a46fe486e6933d) — reproduced against
# the in-memory backend so they run without a live database. Concurrency/
# real-Postgres-only proofs (finding 2's serialized generic-route revision
# lock, finding 8's receipts-table readiness probe) live in
# test_owner_durability.py, gated on TEST_DATABASE_URL.
# ===========================================================================


def _cross_tenant_artifact_registration(*, artifact_id: str) -> dict:
    """A schema-valid StrategyArtifact registration, derived from the
    checked-in builtin (mirrors test_strategy_artifact.py's ``_artifact``/
    ``mutate_strategy_artifact`` pattern) so these tenant-scoping tests
    exercise real schema validation rather than a hand-rolled partial dict."""
    parent = load_strategy_artifact_registration(BUILTIN_STRATEGY_ARTIFACT_PATHS[0])["strategy_artifact"]
    artifact = mutate_strategy_artifact(
        parent,
        new_artifact_id=artifact_id,
        new_version="1.1.0",
        parameter_updates={"momentum_threshold": 0.03},
        source_run_ids=["training-session-tenant-probe"],
    )
    return {
        "registry_id": artifact_id,
        "artifact_state": "candidate",
        "strategy_artifact": artifact,
    }


def test_strategy_artifact_replay_from_different_tenant_is_denied_not_leaked(strict_client):
    """Reviewer finding 1: a same-registry_id StrategyArtifact POST replay
    from a *different* tenant than the entry's true owner must be denied
    (403), not silently authorized and its private content returned."""
    owner_token = _jwt(subject="artifact-owner", tenant="tenant-a")
    other_token = _jwt(subject="artifact-intruder", tenant="tenant-b")
    registration = _cross_tenant_artifact_registration(artifact_id="artifact-cross-tenant-probe")

    created = strict_client.post(
        "/api/registry/strategy-artifacts", json=registration, headers=_bearer(owner_token),
    )
    assert created.status_code == 200, created.text

    denied_get = strict_client.get(
        "/api/registry/strategy-artifacts/artifact-cross-tenant-probe", headers=_bearer(other_token),
    )
    assert denied_get.status_code == 403, denied_get.text

    replay = strict_client.post(
        "/api/registry/strategy-artifacts", json=registration, headers=_bearer(other_token),
    )
    assert replay.status_code == 403, replay.text


def test_strategy_artifact_replay_from_same_tenant_still_succeeds(strict_client):
    """Same-tenant replay of an identical StrategyArtifact registration must
    keep succeeding as an idempotent no-op (not collateral damage from the
    finding-1 cross-tenant fix)."""
    token = _jwt(subject="artifact-owner-2", tenant="tenant-a")
    registration = _cross_tenant_artifact_registration(artifact_id="artifact-same-tenant-probe")
    created = strict_client.post(
        "/api/registry/strategy-artifacts", json=registration, headers=_bearer(token),
    )
    assert created.status_code == 200, created.text

    replay = strict_client.post(
        "/api/registry/strategy-artifacts", json=registration, headers=_bearer(token),
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["entry"]["registry_id"] == "artifact-same-tenant-probe"


def test_strategy_spec_create_replay_after_metadata_edit_still_matches_original(strict_client):
    """Reviewer finding 5: a same-registry_id StrategySpec create replay must
    be compared against the entry's *original* creation content, not
    whatever it has mutated into since a later, unrelated update_metadata
    call — otherwise an exact replay of the original request is wrongly
    rejected as "different content" just because metadata legitimately
    drifted under a separate command."""
    token = _jwt(subject="spec-owner", tenant="tenant-a")
    registry_id = "reg-strategy-spec-metadata-drift-probe"
    payload = {
        "registry_id": registry_id,
        "strategy_id": "spec-metadata-drift-strat",
        "version": "1.0.0",
        "lineage": {"source_run_ids": ["run-1"]},
        "strategy_spec": _valid_spec("spec-metadata-drift-strat"),
    }

    created = strict_client.post(
        "/api/registry/strategy-specs", json=payload, headers=_bearer(token),
    )
    assert created.status_code == 200, created.text
    original_metadata = created.json()["entry"]["metadata"]

    patched = strict_client.patch(
        f"/api/registry/entries/{registry_id}/metadata",
        json={
            "expected_metadata": original_metadata,
            "metadata": dict(original_metadata, operator_note="edited after creation"),
        },
        headers=_bearer(token),
    )
    assert patched.status_code == 200, patched.text

    replay = strict_client.post(
        "/api/registry/strategy-specs", json=payload, headers=_bearer(token),
    )
    assert replay.status_code == 200, replay.text
    # The replay's identity-comparison succeeded (200, not 400) even though
    # the live entry's metadata now differs from what was originally
    # submitted. NOTE: per the gen-10 review's finding 4 (see
    # _ensure_strategy_spec_registration_matches's docstring in service.py),
    # the contract for *what this POST call itself returns* changed after
    # this test was first written: a same-identity create replay's response
    # now reports the original creation snapshot (receipt_entry) rather than
    # the live, possibly-since-mutated view — a caller retrying its own
    # create command expects back exactly what that command committed, not
    # whatever an unrelated later command (the metadata PATCH here) has done
    # to the aggregate since. The durable row itself is never touched by
    # this replay either way, which the readback below still confirms.
    assert "operator_note" not in replay.json()["entry"]["metadata"]

    readback = strict_client.get(
        f"/api/registry/strategy-specs/{registry_id}", headers=_bearer(token),
    )
    assert readback.json()["entry"]["metadata"]["operator_note"] == "edited after creation"


def test_strategy_spec_create_replay_still_rejects_genuinely_different_content(strict_client):
    """The finding-5 fix must not turn the replay comparison into a no-op —
    a same-registry_id create with genuinely different original content
    (never submitted before) must still be rejected (400)."""
    token = _jwt(subject="spec-owner-2", tenant="tenant-a")
    registry_id = "reg-strategy-spec-genuine-collision-probe"
    payload = {
        "registry_id": registry_id,
        "strategy_id": "spec-genuine-collision-strat",
        "version": "1.0.0",
        "lineage": {"source_run_ids": ["run-1"]},
        "strategy_spec": _valid_spec("spec-genuine-collision-strat"),
    }
    created = strict_client.post(
        "/api/registry/strategy-specs", json=payload, headers=_bearer(token),
    )
    assert created.status_code == 200, created.text

    different = dict(payload)
    different["strategy_spec"] = _valid_spec(
        "spec-genuine-collision-strat", caller_note="genuinely different content",
    )
    collision = strict_client.post(
        "/api/registry/strategy-specs", json=different, headers=_bearer(token),
    )
    assert collision.status_code == 400, collision.text


def test_advance_with_stale_expected_base_is_409_not_silently_ignored(strict_client):
    """Reviewer finding 6: an advance request carrying an explicit,
    caller-claimed base (expected_artifact_state/expected_version/
    expected_updated_at) that is stale must be rejected (409), not silently
    committed against whatever the row actually is regardless of the
    caller's false premise."""
    token = _jwt(subject="advance-base-writer", tenant="tenant-a")
    created = _create_entry(strict_client, token, strategy_id="advance-stale-base-strat")
    registry_id = created["entry"]["registry_id"]
    original_version = created["entry"]["version"]
    original_updated_at = created["entry"]["updated_at"]

    first = advance_registry_http(
        strict_client, f"/api/registry/entries/{registry_id}/advance",
        json={"target_state": "candidate", "expected_artifact_state": "draft"},
        headers=_bearer(token),
    )
    assert first.status_code == 200, first.text

    stale = advance_registry_http(
        strict_client, f"/api/registry/entries/{registry_id}/advance",
        json={
            "target_state": "approved",
            "expected_artifact_state": "draft",
            "expected_version": original_version,
            "expected_updated_at": original_updated_at,
        },
        headers=_bearer(token),
    )
    assert stale.status_code == 409, stale.text

    unchanged = strict_client.get(f"/api/registry/entries/{registry_id}", headers=_bearer(token))
    assert unchanged.json()["entry"]["artifact_state"] == "candidate"


def test_advance_with_matching_expected_base_succeeds(strict_client):
    """The finding-6 fix must not reject a caller that supplies a base which
    genuinely matches the current durable row."""
    token = _jwt(subject="advance-base-writer-2", tenant="tenant-a")
    created = _create_entry(strict_client, token, strategy_id="advance-fresh-base-strat")
    registry_id = created["entry"]["registry_id"]
    entry = created["entry"]

    advanced = advance_registry_http(
        strict_client, f"/api/registry/entries/{registry_id}/advance",
        json={
            "target_state": "candidate",
            "expected_artifact_state": entry["artifact_state"],
            "expected_version": entry["version"],
            "expected_updated_at": entry["updated_at"],
        },
        headers=_bearer(token),
    )
    assert advanced.status_code == 200, advanced.text
    assert advanced.json()["entry"]["artifact_state"] == "candidate"


def test_metadata_and_advance_command_keys_do_not_share_a_receipt_namespace(strict_client):
    """Reviewer finding 6: the same client-chosen command_key value used for
    a metadata-CAS call and, separately, an artifact-state advance on the
    same registry_id/tenant/actor must never be treated as one receipt
    namespace — each command kind gets its own scoped receipt row."""
    token = _jwt(subject="namespace-writer", tenant="tenant-a")
    created = _create_entry(strict_client, token, strategy_id="receipt-namespace-strat")
    registry_id = created["entry"]["registry_id"]
    shared_key = "shared-command-key-001"

    metadata_call = strict_client.patch(
        f"/api/registry/entries/{registry_id}/metadata",
        json={"expected_metadata": None, "metadata": {"note": "v1"}, "command_key": shared_key},
        headers=_bearer(token),
    )
    assert metadata_call.status_code == 200, metadata_call.text
    assert metadata_call.headers["X-Idempotent-Replay"] == "false"

    advance_call = advance_registry_http(
        strict_client, f"/api/registry/entries/{registry_id}/advance",
        json={
            "target_state": "candidate",
            "command_key": shared_key,
            "expected_artifact_state": "draft",
        },
        headers=_bearer(token),
    )
    assert advance_call.status_code == 200, advance_call.text
    assert advance_call.json()["entry"]["artifact_state"] == "candidate"

    # Both commands actually took effect — neither one was treated as a
    # (wrong-type) replay of the other's receipt.
    final = strict_client.get(f"/api/registry/entries/{registry_id}", headers=_bearer(token))
    final_entry = final.json()["entry"]
    assert final_entry["metadata"] == {"note": "v1"}
    assert final_entry["artifact_state"] == "candidate"
