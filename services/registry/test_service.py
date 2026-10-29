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
        with pytest.raises(RegistryError):
            svc.resolve_deployment_view("nonexistent")
        view = svc.resolve_deployment_view(
            "nonexistent",
            runtime_bindings_reader=lambda _tid: [],
            deployment_plan_reader=lambda _pid, _tid: None,
        )
        assert view.current_stage == DeploymentStage.NONE

    def test_approved_without_deployment_summary(self):
        store = RegistryStore()
        svc = RegistryService(store)
        svc.register(_make_create_payload(), "reg-001", actor={"id": "unit-operator", "tenant": "tenant-unit"})
        advance_registry_unit(svc, "reg-001", ArtifactState.CANDIDATE)
        advance_registry_unit(svc, "reg-001", ArtifactState.APPROVED)
        # Cached deployment_summary on RegistryEntry is non-authoritative and never read by resolve_deployment_view.
        svc.update_deployment_summary(
            "reg-001",
            current_stage=DeploymentStage.CANARY,
            deployment_plan_id="cached-plan",
            runtime_binding_id="cached-rb",
        )

        view = svc.resolve_deployment_view(
            "test-alpha",
            tenant_id="tenant-unit",
            runtime_bindings_reader=lambda _tid: [],
            deployment_plan_reader=lambda _pid, _tid: None,
        )
        assert view.current_stage == DeploymentStage.NONE
        assert view.latest_approved_registry_id == "reg-001"
        assert view.deployment_plan_id is None
        assert view.runtime_binding_id is None

    def test_approved_with_deployment_summary(self):
        store = RegistryStore()
        svc = RegistryService(
            store,
            runtime_bindings_reader=lambda _tid: [
                {
                    "binding_id": "rb-001",
                    "capital_pool_id": "pool-unit",
                    "artifact_id": "reg-001",
                    "artifact_version": "1.0.0",
                    "deployment_mode": "canary",
                    "plan_id": "plan-001",
                    "status": "active",
                    "effective_at": "2026-10-18T00:00:00Z",
                    "metadata": {"tenant_id": "tenant-unit", "strategy_id": "test-alpha"},
                }
            ],
            deployment_plan_reader=lambda pid, _tid: {
                "plan_id": pid,
                "strategy_id": "test-alpha",
                "capital_pool_id": "pool-unit",
                "artifact_id": "reg-001",
                "artifact_version": "1.0.0",
                "target_stage": "canary",
                "status": "approved",
                "tenant_id": "tenant-unit",
                "binding_id": "rb-001",
            },
        )
        svc.register(_make_create_payload(), "reg-001", actor={"id": "unit-operator", "tenant": "tenant-unit"})
        advance_registry_unit(svc, "reg-001", ArtifactState.CANDIDATE)
        advance_registry_unit(svc, "reg-001", ArtifactState.APPROVED)

        view = svc.resolve_deployment_view("test-alpha", tenant_id="tenant-unit")
        assert view.current_stage == DeploymentStage.CANARY
        assert view.deployment_plan_id == "plan-001"
        assert view.runtime_binding_id == "rb-001"
        assert view.last_transition_at == "2026-10-18T00:00:00Z"

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

    def test_deployment_view(self, monkeypatch):
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
        owner_tenant = create_resp.json()["entry"]["owner_tenant"]
        advance_registry_http(
            self.client, f"/api/registry/entries/{rid}/advance",
            json={"target_state": "candidate", "expected_artifact_state": "draft"},
        )
        advance_registry_http(
            self.client, f"/api/registry/entries/{rid}/advance",
            json={"target_state": "approved", "expected_artifact_state": "candidate"},
        )

        with _local_owner_server(
            bindings=[
                {
                    "binding_id": "rb-001",
                    "capital_pool_id": "pool-001",
                    "artifact_id": rid,
                    "artifact_version": "1.0.0",
                    "deployment_mode": "paper",
                    "plan_id": "plan-001",
                    "status": "active",
                    "effective_at": "2026-10-18T00:00:00Z",
                    "metadata": {"tenant_id": owner_tenant, "strategy_id": "dv-test"},
                }
            ],
            plans={
                "plan-001": {
                    "plan_id": "plan-001",
                    "strategy_id": "dv-test",
                    "capital_pool_id": "pool-001",
                    "artifact_id": rid,
                    "artifact_version": "1.0.0",
                    "target_stage": "paper",
                    "status": "approved",
                    "tenant_id": owner_tenant,
                    "binding_id": "rb-001",
                }
            },
        ) as (base_url, _state):
            monkeypatch.setenv("PANTHEON_RUNTIME_MANAGER_URL", base_url)
            monkeypatch.setenv("PANTHEON_DEPLOYMENT_API_URL", base_url)

            resp = self.client.get("/api/registry/strategies/dv-test/deployment-view")
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["current_stage"] == "paper"
            assert data["deployment_plan_id"] == "plan-001"
            assert data["runtime_binding_id"] == "rb-001"
            assert data["latest_approved_registry_id"] == rid

    def test_deployment_view_empty(self, monkeypatch):
        monkeypatch.delenv("PANTHEON_RUNTIME_MANAGER_URL", raising=False)
        monkeypatch.delenv("PANTHEON_DEPLOYMENT_API_URL", raising=False)
        missing_resp = self.client.get("/api/registry/strategies/nonexistent/deployment-view")
        assert missing_resp.status_code == 503

        with _local_owner_server(bindings=[], plans={}) as (base_url, _state):
            monkeypatch.setenv("PANTHEON_RUNTIME_MANAGER_URL", base_url)
            monkeypatch.setenv("PANTHEON_DEPLOYMENT_API_URL", base_url)
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
    with TestClient(app) as client:
        yield client


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
    with TestClient(app) as fresh:
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


import atexit
from contextlib import contextmanager
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading

from .storage import get_store

_REAL_OWNER_MODS: dict[str, object] = {}


def _get_real_owner_apps():
    if not _REAL_OWNER_MODS:
        boot_tmpdir = tempfile.TemporaryDirectory(prefix="pantheon-registry-owner-boot-")
        atexit.register(boot_tmpdir.cleanup)
        boot_root = Path(boot_tmpdir.name).resolve()
        gov_dir = boot_root / "governance"
        rm_data_dir = boot_root / "runtime-manager"
        cap_dir = boot_root / "capital"
        rm_store_path = rm_data_dir / "bindings.json"
        cap_pool_path = cap_dir / "capital_pools.json"
        persona_binding_path = cap_dir / "persona_capital_bindings.json"

        boot_env = {
            "DEPLOYMENT_DATA_DIR": str(gov_dir),
            "PANTHEON_GOVERNANCE_DATA_DIR": str(gov_dir),
            "CAPITAL_DATA_DIR": str(cap_dir),
            "PANTHEON_RUNTIME_DATA_DIR": str(rm_data_dir),
            "PANTHEON_RUNTIME_BINDING_STORE_PATH": str(rm_store_path),
            "PANTHEON_CAPITAL_POOL_STORE_PATH": str(cap_pool_path),
            "PANTHEON_PERSONA_BINDING_STORE_PATH": str(persona_binding_path),
        }
        saved_boot_env = {k: os.environ.get(k) for k in boot_env}
        for k, v in boot_env.items():
            os.environ[k] = v
        try:
            rm_dir = str(Path(__file__).resolve().parents[1] / "runtime-manager")
            if rm_dir not in sys.path:
                sys.path.insert(0, rm_dir)
            spec = importlib.util.spec_from_file_location(
                "pantheon_runtime_manager_main_real",
                Path(rm_dir) / "main.py",
            )
            assert spec is not None and spec.loader is not None
            rm_main = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(rm_main)
            from services.deployment import service as dep_svc
            from services.deployment.outbox_lease import DeploymentOutboxLeaseStore
            from services.runtime_manager.runtime_binding import RuntimeBinding, RuntimeBindingStore
            from deployment_plan import DeploymentPlan, DeploymentPlanStore
            from deployment_saga import DeploymentSagaStore
        finally:
            for k, v in saved_boot_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

        assert Path(dep_svc.DATA_DIR).resolve() == gov_dir
        assert Path(dep_svc.PLAN_STORE_PATH).resolve() == gov_dir / "deployment_plans.json"
        assert Path(dep_svc.SAGA_STORE_PATH).resolve() == gov_dir / "deployment_sagas.json"
        assert Path(dep_svc.OUTBOX_LEASE_STORE_PATH).resolve() == gov_dir / "deployment_outbox_leases.json"
        assert Path(dep_svc.RUNTIME_BINDING_STORE_PATH).resolve() == rm_store_path
        assert Path(dep_svc.CAPITAL_POOL_STORE_PATH).resolve() == cap_pool_path
        assert Path(dep_svc.PERSONA_BINDING_STORE_PATH).resolve() == persona_binding_path
        assert Path(rm_main._STORE_PATH_ENV).resolve() == rm_store_path
        assert "/tmp/pantheon/governance" not in str(dep_svc.DATA_DIR)
        assert "/tmp/pantheon/runtime-manager" not in str(rm_main._STORE_PATH_ENV)

        startup_isolation = {
            "verified": True,
            "boot_root": str(boot_root),
            "deployment_data_dir": str(Path(dep_svc.DATA_DIR).resolve()),
            "plan_store_path": str(Path(dep_svc.PLAN_STORE_PATH).resolve()),
            "saga_store_path": str(Path(dep_svc.SAGA_STORE_PATH).resolve()),
            "outbox_lease_store_path": str(Path(dep_svc.OUTBOX_LEASE_STORE_PATH).resolve()),
            "runtime_binding_store_path": str(Path(rm_main._STORE_PATH_ENV).resolve()),
            "capital_pool_store_path": str(Path(dep_svc.CAPITAL_POOL_STORE_PATH).resolve()),
            "persona_binding_store_path": str(Path(dep_svc.PERSONA_BINDING_STORE_PATH).resolve()),
        }

        _REAL_OWNER_MODS.update(
            _boot_tmpdir=boot_tmpdir,
            startup_isolation=startup_isolation,
            rm_main=rm_main,
            dep_svc=dep_svc,
            RuntimeBinding=RuntimeBinding,
            RuntimeBindingStore=RuntimeBindingStore,
            DeploymentPlan=DeploymentPlan,
            DeploymentPlanStore=DeploymentPlanStore,
            DeploymentSagaStore=DeploymentSagaStore,
            DeploymentOutboxLeaseStore=DeploymentOutboxLeaseStore,
        )
    return _REAL_OWNER_MODS


def _snapshot_owner_and_registry_state(reg_store, rm_store, plan_store, saga_store) -> dict:
    return {
        "registry_entries": {
            rid: entry.to_dict()
            for rid, entry in sorted(reg_store._entries.items())
        },
        "registry_command_receipts": copy.deepcopy(dict(sorted(reg_store._command_receipts.items()))),
        "registry_creation_receipts": copy.deepcopy(dict(sorted(reg_store._creation_receipts.items()))),
        "runtime_bindings": [
            b.to_dict()
            for b in sorted(rm_store.list_all(), key=lambda item: item.binding_id)
        ],
        "deployment_plans": [
            p.to_dict()
            for p in sorted(plan_store.list_all(), key=lambda item: item.plan_id)
        ],
        "deployment_sagas": [
            s.to_dict()
            for s in sorted(saga_store.list_all(), key=lambda item: item.saga_id)
        ],
        "deployment_outbox_records": [
            r.to_dict()
            for r in saga_store.outbox_records()
        ],
        "deployment_inbox_receipts": [
            r.to_dict()
            for r in saga_store.inbox_receipts()
        ],
    }


def _coerce_runtime_binding(RuntimeBinding, raw: dict):
    if isinstance(raw, RuntimeBinding):
        return raw
    data = dict(raw)
    meta = dict(data.get("metadata") or {})
    if "tenant_id" in data and "tenant_id" not in meta and data["tenant_id"] is not None:
        meta["tenant_id"] = data["tenant_id"]
    if "strategy_id" in data and "strategy_id" not in meta and data["strategy_id"] is not None:
        meta["strategy_id"] = data["strategy_id"]
    data["metadata"] = meta
    data.setdefault("runtime_id", f"rt-{data.get('binding_id', 'unit')}")
    data.setdefault("persona_capital_binding_id", "pcb-unit-001")
    data.setdefault("effective_at", "2026-10-18T00:00:00Z")
    return RuntimeBinding.from_dict(data)


def _coerce_deployment_plan(DeploymentPlan, pid: str, raw: dict):
    if isinstance(raw, DeploymentPlan):
        return raw
    data = dict(raw)
    meta = dict(data.get("metadata") or {})
    if "tenant_id" in data:
        if data["tenant_id"] is not None:
            meta["tenant_id"] = data["tenant_id"]
        else:
            meta.pop("tenant_id", None)
    data["metadata"] = meta
    data.setdefault("plan_id", pid)
    data.setdefault("approval_decision_id", f"decision-{pid}")
    data.setdefault("artifact_type", "execution_bundle")
    data.setdefault("current_stage", "none")
    data.setdefault("transition_type", "activate")
    data.setdefault("runtime_action", "deploy_new_binding")
    data.setdefault("created_at", "2026-10-18T00:00:00Z")
    return DeploymentPlan.from_dict(data)


@contextmanager
def _local_owner_server(*, bindings: list[dict], plans: dict[str, dict], filter_by_tenant: bool = True):
    mods = _get_real_owner_apps()
    rm_main = mods["rm_main"]
    dep_svc = mods["dep_svc"]
    RuntimeBinding = mods["RuntimeBinding"]
    RuntimeBindingStore = mods["RuntimeBindingStore"]
    DeploymentPlan = mods["DeploymentPlan"]
    DeploymentPlanStore = mods["DeploymentPlanStore"]
    DeploymentSagaStore = mods["DeploymentSagaStore"]

    rm_store = RuntimeBindingStore(path=None)
    plan_store = DeploymentPlanStore(storage_path=None)
    saga_store = DeploymentSagaStore(storage_path=None)

    class _InMemoryRuntimeService:
        def __init__(self, store):
            self._store = store

        def list_all(self):
            return self._store.list_all()

        def list_by_pool(self, pool_id: str):
            return self._store.find_by_pool(pool_id)

        def list_by_plan(self, plan_id: str):
            return self._store.find_by_plan(plan_id)

        def get(self, binding_id: str):
            return self._store.get(binding_id)

    prev_rm_svc = rm_main._svc
    prev_dep_store = dep_svc.store
    prev_planner_store = dep_svc.planner_service.plan_store
    prev_saga_store = dep_svc.saga_store
    prev_orch_saga_store = dep_svc.orchestration_service.saga_store
    prev_proj_saga_store = dep_svc.projection_service.saga_store
    rm_main._svc = _InMemoryRuntimeService(rm_store)
    dep_svc.store = plan_store
    dep_svc.planner_service.plan_store = plan_store
    dep_svc.saga_store = saga_store
    dep_svc.orchestration_service.saga_store = saga_store
    dep_svc.projection_service.saga_store = saga_store

    env_keys = (
        "PANTHEON_RUNTIME_AUTH_MODE",
        "PANTHEON_RUNTIME_JWT_SECRET",
        "PANTHEON_RUNTIME_JWT_ISSUER",
        "PANTHEON_RUNTIME_JWT_AUDIENCE",
        "PANTHEON_DEPLOYMENT_AUTH_MODE",
        "PANTHEON_DEPLOYMENT_JWT_SECRET",
        "PANTHEON_DEPLOYMENT_JWT_ISSUER",
        "PANTHEON_DEPLOYMENT_JWT_AUDIENCE",
    )
    saved_env = {k: os.environ.get(k) for k in env_keys}
    reg_secret = os.environ.get("PANTHEON_REGISTRY_JWT_SECRET", _JWT_SECRET)
    reg_issuer = os.environ.get("PANTHEON_REGISTRY_JWT_ISSUER", "")
    reg_audience = os.environ.get("PANTHEON_REGISTRY_JWT_AUDIENCE", "")
    os.environ["PANTHEON_RUNTIME_AUTH_MODE"] = "strict"
    os.environ["PANTHEON_RUNTIME_JWT_SECRET"] = reg_secret
    os.environ["PANTHEON_RUNTIME_JWT_ISSUER"] = reg_issuer
    os.environ["PANTHEON_RUNTIME_JWT_AUDIENCE"] = reg_audience
    os.environ["PANTHEON_DEPLOYMENT_AUTH_MODE"] = "strict"
    os.environ["PANTHEON_DEPLOYMENT_JWT_SECRET"] = reg_secret
    os.environ["PANTHEON_DEPLOYMENT_JWT_ISSUER"] = reg_issuer
    os.environ["PANTHEON_DEPLOYMENT_JWT_AUDIENCE"] = reg_audience

    rm_client = rm_main.app.test_client()
    dep_client = TestClient(dep_svc.app)

    class _OwnerFixtureState(dict):
        def __setitem__(self, key, value):
            super().__setitem__(key, value)
            if key in ("bindings", "plans") and self.get("_ready"):
                _seed_stores()

    state = _OwnerFixtureState(
        bindings=list(bindings),
        plans=dict(plans),
        filter_by_tenant=filter_by_tenant,
        requests=[],
        rm_store=rm_store,
        plan_store=plan_store,
        saga_store=saga_store,
        startup_isolation=mods["startup_isolation"],
        use_raw_rm_store=False,
        seed_count=0,
        _ready=False,
    )

    def _seed_stores():
        if not state.get("use_raw_rm_store"):
            rm_store._bindings = {
                b.binding_id: b
                for b in (_coerce_runtime_binding(RuntimeBinding, item) for item in state["bindings"])
            }
        plan_store._plans = {
            pid: _coerce_deployment_plan(DeploymentPlan, pid, p_raw)
            for pid, p_raw in state["plans"].items()
        }
        dict.__setitem__(state, "seed_count", int(state.get("seed_count", 0)) + 1)

    _seed_stores()
    seeded_plans = plan_store.list_all()
    if seeded_plans:
        first_plan = seeded_plans[0]
        boot = saga_store.bootstrap_for_plan(
            first_plan,
            trace_id=f"trace-{first_plan.plan_id}",
            saga_id=f"saga-{first_plan.plan_id}",
            metadata=dict(first_plan.metadata or {}),
        )
        saga_store.consume_event("runtime-manager", boot.outbox_event.event)
    dict.__setitem__(state, "_ready", True)
    dict.__setitem__(
        state,
        "snapshot",
        lambda reg_store=None: _snapshot_owner_and_registry_state(
            reg_store or get_store(),
            rm_store,
            plan_store,
            saga_store,
        ),
    )

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def do_GET(self):
            req_auth = self.headers.get("Authorization")
            req_tenant = self.headers.get("X-Tenant-Id")
            state["requests"].append(
                {
                    "method": "GET",
                    "path": self.path,
                    "authorization": req_auth,
                    "tenant": req_tenant,
                }
            )
            fwd_headers = {}
            if req_auth is not None:
                fwd_headers["Authorization"] = req_auth
            if req_tenant is not None and state["filter_by_tenant"]:
                fwd_headers["X-Tenant-Id"] = req_tenant

            if self.path.startswith("/api/runtime-bindings"):
                resp = rm_client.get(self.path, headers=fwd_headers)
                self.send_response(resp.status_code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(resp.get_data())
                return
            if self.path.startswith("/api/deployment/"):
                resp = dep_client.get(self.path, headers=fwd_headers)
                self.send_response(resp.status_code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(resp.content)
                return
            self.send_response(404)
            self.end_headers()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}", state
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=2.0)
        rm_main._svc = prev_rm_svc
        dep_svc.store = prev_dep_store
        dep_svc.planner_service.plan_store = prev_planner_store
        dep_svc.saga_store = prev_saga_store
        dep_svc.orchestration_service.saga_store = prev_orch_saga_store
        dep_svc.projection_service.saga_store = prev_proj_saga_store
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_deployment_view_composes_from_runtime_binding_and_deployment_plan_Across_family_artifacts(
    strict_client, monkeypatch
):
    """REGISTRY-DEPLOYMENT-VIEW-OWNER-READ-20261018:
    - Composes current_stage, deployment_plan_id, runtime_binding_id, and last_transition_at
      from canonical RuntimeBinding (GET /api/runtime-bindings) + DeploymentPlan
      (GET /api/deployment/plans/{plan_id}) over real HTTP against the actual runtime-manager
      and deployment service apps while keeping latest_approved_* on the newest caller-visible
      approved Registry revision.
    - Ignores absent, stale, or conflicting entry.deployment_summary cache values.
    - Supports retirement -> none, rollback/reactivation binding + plan readback,
      paused/pending_pause states, legal paused + active bindings in the same
      single-runtime-enforced pool, and newest-transition ordering across distinct valid pools.
    - Fails closed with 503 on contradictory multiple active bindings in the same pool,
      missing/rejected/mismatched DeploymentPlan (plan_id, capital_pool_id, binding_id,
      artifact_id, artifact_version, strategy_id, tenant_id, target_stage != deployment_mode
      including paper vs frozen), or current RuntimeBinding mismatched on version/strategy/tenant/non-approved state.
    - Immutable EntryGET makes zero outbound owner callbacks, and repeated read-only calls leave
      Registry and owner state unchanged.
    """
    strategy_id = "strategy-persona-99a8bc642bfcb4df0c5f"
    spec_id_v1 = "reg-strategy-spec-persona-99a8bc642bfcb4df0c5f"
    spec_id_v2 = "reg-strategy-spec-persona-99a8bc642bfcb4df0c5f-v2"
    bundle_id = "artifact-persona-paper-99a8bc642bfcb4df0c5f"
    plan_id = "plan-persona-paper-99a8bc642bfcb4df0c5f"
    binding_id = "rb-87d5f907703249ca874df9dfd2ab96ef"
    pool_a = "pool-persona-a"
    pool_b = "pool-persona-b"

    token_a = _jwt(subject="persona-op", tenant="tenant-a")
    token_b = _jwt(subject="other-tenant-op", tenant="tenant-b")

    # 1. Register + approve strategy_spec v1.0.0 and execution_bundle v1.0.0 in tenant-a
    spec_v1 = strict_client.post(
        "/api/registry/strategy-specs",
        json={
            "registry_id": spec_id_v1,
            "strategy_id": strategy_id,
            "version": "1.0.0",
            "source_seed_id": "source-seed-001",
            "strategy_spec": _minimal_strategy_spec(strategy_id),
        },
        headers=_bearer(token_a),
    )
    assert spec_v1.status_code == 200, spec_v1.text
    for target, expected in (("candidate", "draft"), ("approved", "candidate")):
        r = advance_registry_http(
            strict_client,
            f"/api/registry/strategy-specs/{spec_id_v1}/advance",
            json={"target_state": target, "expected_artifact_state": expected},
            headers=_bearer(token_a),
        )
        assert r.status_code == 200, r.text

    bundle = strict_client.post(
        "/api/registry/entries",
        json={
            "artifact_type": "execution_bundle",
            "strategy_id": strategy_id,
            "version": "1.0.0",
            "storage_ref": {"backend": "inline", "path": f"inline://execution-bundles/{bundle_id}"},
            "checksum": "sha256:bundle-100",
            "lineage": {"parent_registry_ids": [spec_id_v1], "source_strategy_spec_id": spec_id_v1},
        },
        headers=_bearer(token_a),
    )
    assert bundle.status_code == 200, bundle.text
    bundle_rid = bundle.json()["entry"]["registry_id"]
    for target, expected in (("candidate", "draft"), ("approved", "candidate")):
        r = advance_registry_http(
            strict_client,
            f"/api/registry/entries/{bundle_rid}/advance",
            json={
                "target_state": target,
                "expected_artifact_state": expected,
                "command_key": f"cmd-advance-{bundle_rid}-{target}",
            },
            headers=_bearer(token_a),
        )
        assert r.status_code == 200, r.text
    assert r.json()["entry"]["deployment_summary"] is None

    # Register + approve newer strategy_spec 2.0.0 (not yet deployed) and seed conflicting deployment_summary cache
    spec_v2_payload = _minimal_strategy_spec(strategy_id)
    spec_v2_payload["title"] = "Distilled momentum hypothesis v2"
    spec_v2 = strict_client.post(
        "/api/registry/strategy-specs",
        json={
            "registry_id": spec_id_v2,
            "strategy_id": strategy_id,
            "version": "2.0.0",
            "source_seed_id": "source-seed-001",
            "lineage": {"parent_registry_ids": [spec_id_v1], "source_run_ids": ["source-seed-001"]},
            "base_checksum": spec_v1.json()["entry"]["checksum"],
            "strategy_spec": spec_v2_payload,
        },
        headers=_bearer(token_a),
    )
    assert spec_v2.status_code == 200, spec_v2.text
    for target, expected in (("candidate", "draft"), ("approved", "candidate")):
        r = advance_registry_http(
            strict_client,
            f"/api/registry/strategy-specs/{spec_id_v2}/advance",
            json={"target_state": target, "expected_artifact_state": expected},
            headers=_bearer(token_a),
        )
        assert r.status_code == 200, r.text
    stale_cache = strict_client.put(
        f"/api/registry/entries/{spec_id_v2}/deployment-summary",
        json={"current_stage": "live", "deployment_plan_id": "stale-cached-plan", "runtime_binding_id": "stale-cached-rb"},
        headers=_bearer(token_a),
    )
    assert stale_cache.status_code == 200, stale_cache.text

    # Also register a draft entry for the same strategy to test non-approved binding rejection
    draft_resp = strict_client.post(
        "/api/registry/entries",
        json={
            "artifact_type": "model_artifact",
            "strategy_id": strategy_id,
            "version": "3.0.0",
            "storage_ref": {"backend": "object_store", "path": "s3://bucket/draft-300.bin"},
            "checksum": "sha256:draft-300",
            "lineage": {"source_run_ids": ["run-300"]},
        },
        headers=_bearer(token_a),
    )
    assert draft_resp.status_code == 200, draft_resp.text
    draft_rid = draft_resp.json()["entry"]["registry_id"]

    active_binding_a = {
        "binding_id": binding_id,
        "capital_pool_id": pool_a,
        "artifact_id": bundle_rid,
        "artifact_version": "1.0.0",
        "deployment_mode": "paper",
        "plan_id": plan_id,
        "status": "active",
        "effective_at": "2026-10-17T22:27:53Z",
        "metadata": {"tenant_id": "tenant-a", "strategy_id": strategy_id},
    }
    plan_a = {
        "plan_id": plan_id,
        "strategy_id": strategy_id,
        "capital_pool_id": pool_a,
        "artifact_id": bundle_rid,
        "artifact_version": "1.0.0",
        "target_stage": "paper",
        "status": "approved",
        "tenant_id": "tenant-a",
        "binding_id": None,
    }

    with _local_owner_server(
        bindings=[
            {
                "binding_id": "rb-retired-old",
                "capital_pool_id": pool_a,
                "artifact_id": bundle_rid,
                "artifact_version": "1.0.0",
                "deployment_mode": "live",
                "plan_id": "plan-retired",
                "status": "retired",
                "retired_at": "2026-10-17T20:00:00Z",
                "effective_at": "2026-10-17T19:00:00Z",
                "metadata": {"tenant_id": "tenant-a", "strategy_id": strategy_id},
            },
            {
                "binding_id": "rb-other-strategy",
                "capital_pool_id": "pool-other",
                "artifact_id": "other-artifact",
                "artifact_version": "1.0.0",
                "deployment_mode": "canary",
                "plan_id": "plan-other",
                "status": "active",
                "effective_at": "2026-10-17T23:59:59Z",
                "metadata": {"tenant_id": "tenant-a", "strategy_id": "other-strategy"},
            },
            dict(active_binding_a),
        ],
        plans={plan_id: dict(plan_a)},
    ) as (base_url, state):
        assert state["startup_isolation"]["verified"] is True
        assert state["seed_count"] == 1
        monkeypatch.setenv("PANTHEON_RUNTIME_MANAGER_URL", base_url)
        monkeypatch.setenv("PANTHEON_DEPLOYMENT_API_URL", base_url)
        # Process-global tenant env vars must never override the verified caller tenant
        monkeypatch.setenv("PANTHEON_DEFAULT_TENANT", "tenant-other")
        monkeypatch.setenv("PANTHEON_TENANT_ID", "tenant-other")

        baseline_snapshot = state["snapshot"]()
        assert {spec_id_v1, bundle_rid, spec_id_v2, draft_rid}.issubset(
            baseline_snapshot["registry_entries"]
        )
        assert len(baseline_snapshot["registry_command_receipts"]) >= 2
        assert len(baseline_snapshot["registry_creation_receipts"]) >= 2
        assert len(baseline_snapshot["runtime_bindings"]) == 3
        assert len(baseline_snapshot["deployment_plans"]) == 1
        assert len(baseline_snapshot["deployment_sagas"]) == 1
        assert len(baseline_snapshot["deployment_outbox_records"]) == 1
        assert len(baseline_snapshot["deployment_inbox_receipts"]) == 1

        # Foreign caller X-Tenant-Id header conflicting with verified JWT tenant is rejected (403)
        foreign_hdr_resp = strict_client.get(
            f"/api/registry/strategies/{strategy_id}/deployment-view",
            headers={**_bearer(token_a), "X-Tenant-Id": "tenant-b"},
        )
        assert foreign_hdr_resp.status_code == 403, foreign_hdr_resp.text
        assert state["requests"] == []
        assert state["snapshot"]() == baseline_snapshot

        # Immutable EntryGET makes zero outbound owner calls and leaves all stores/receipts unchanged
        state["requests"].clear()
        entry_read = strict_client.get(f"/api/registry/entries/{bundle_rid}", headers=_bearer(token_a))
        assert entry_read.status_code == 200, entry_read.text
        assert state["requests"] == []
        assert state["seed_count"] == 1
        assert state["snapshot"]() == baseline_snapshot

        # Tenant A sees composed paper deployment from execution_bundle 1.0.0 and latest approved 2.0.0 (ignoring stale cache on v2)
        entries_before = strict_client.get(
            f"/api/registry/strategies/{strategy_id}/entries", headers=_bearer(token_a)
        ).json()
        resp_a = strict_client.get(
            f"/api/registry/strategies/{strategy_id}/deployment-view",
            headers=_bearer(token_a),
        )
        assert resp_a.status_code == 200, resp_a.text
        assert resp_a.json() == {
            "strategy_id": strategy_id,
            "latest_approved_registry_id": spec_id_v2,
            "latest_approved_version": "2.0.0",
            "current_stage": "paper",
            "deployment_plan_id": plan_id,
            "runtime_binding_id": binding_id,
            "last_transition_at": "2026-10-17T22:27:53Z",
        }
        assert [(r["method"], r["path"]) for r in state["requests"]] == [
            ("GET", "/api/runtime-bindings"),
            ("GET", f"/api/deployment/plans/{plan_id}"),
        ]
        assert all(r["authorization"] == f"Bearer {token_a}" and r["tenant"] == "tenant-a" for r in state["requests"])
        assert state["seed_count"] == 1
        assert state["snapshot"]() == baseline_snapshot
        entries_after = strict_client.get(
            f"/api/registry/strategies/{strategy_id}/entries", headers=_bearer(token_a)
        ).json()
        assert entries_after == entries_before

        # Repeated read-only deployment-view call without reseeding preserves identical owner/registry rows and receipts
        resp_a_repeat = strict_client.get(
            f"/api/registry/strategies/{strategy_id}/deployment-view",
            headers=_bearer(token_a),
        )
        assert resp_a_repeat.status_code == 200, resp_a_repeat.text
        assert resp_a_repeat.json() == resp_a.json()
        assert state["seed_count"] == 1
        assert state["snapshot"]() == baseline_snapshot

        # Tenant B never sees Tenant A's approved artifacts or active binding
        resp_b = strict_client.get(
            f"/api/registry/strategies/{strategy_id}/deployment-view",
            headers=_bearer(token_b),
        )
        assert resp_b.status_code == 200, resp_b.text
        assert resp_b.json() == {
            "strategy_id": strategy_id,
            "latest_approved_registry_id": None,
            "latest_approved_version": None,
            "current_stage": "none",
            "deployment_plan_id": None,
            "runtime_binding_id": None,
            "last_transition_at": None,
        }
        assert state["seed_count"] == 1
        assert state["snapshot"]() == baseline_snapshot

        # If a broken owner leaks a foreign-tenant binding to Tenant B, fail closed with 503
        state["filter_by_tenant"] = False
        resp_b_leaked = strict_client.get(
            f"/api/registry/strategies/{strategy_id}/deployment-view",
            headers=_bearer(token_b),
        )
        assert resp_b_leaked.status_code == 503, resp_b_leaked.text
        assert state["seed_count"] == 1
        assert state["snapshot"]() == baseline_snapshot
        state["filter_by_tenant"] = True

        # Operational status='paused' with deployment_mode='paper' and target_stage='paper' stays 'paper' (not 'frozen')
        state["bindings"] = [
            {
                **active_binding_a,
                "status": "paused",
                "metadata": {
                    "tenant_id": "tenant-a",
                    "strategy_id": strategy_id,
                    "paused_at": "2026-10-18T00:30:00Z",
                },
            }
        ]
        resp_paused_paper = strict_client.get(
            f"/api/registry/strategies/{strategy_id}/deployment-view",
            headers=_bearer(token_a),
        )
        assert resp_paused_paper.status_code == 200, resp_paused_paper.text
        assert resp_paused_paper.json()["current_stage"] == "paper"
        assert resp_paused_paper.json()["runtime_binding_id"] == binding_id
        assert resp_paused_paper.json()["last_transition_at"] == "2026-10-18T00:30:00Z"

        # Canonical RuntimeBindingStore(path=None) allows a paused binding and one active binding in the SAME pool
        mods = _get_real_owner_apps()
        RuntimeBinding = mods["RuntimeBinding"]
        rm_store = state["rm_store"]
        rm_store._bindings.clear()
        state["use_raw_rm_store"] = True
        rm_store.create(
            _coerce_runtime_binding(
                RuntimeBinding,
                {
                    **active_binding_a,
                    "binding_id": "synthetic-legal-paused-binding",
                    "plan_id": plan_id,
                    "status": "active",
                    "effective_at": "2026-10-18T00:00:00Z",
                    "metadata": {
                        "tenant_id": "tenant-a",
                        "strategy_id": strategy_id,
                        "last_transition_at": "2026-10-18T00:05:00Z",
                    },
                },
            ),
            single_runtime_enforced=True,
        )
        rm_store.transition_status("synthetic-legal-paused-binding", "pending_pause")
        rm_store.transition_status("synthetic-legal-paused-binding", "paused")
        rm_store.create(
            _coerce_runtime_binding(
                RuntimeBinding,
                {
                    "binding_id": "synthetic-legal-active-binding",
                    "capital_pool_id": pool_a,
                    "artifact_id": spec_id_v2,
                    "artifact_version": "2.0.0",
                    "deployment_mode": "canary",
                    "plan_id": "plan-same-pool-active",
                    "status": "active",
                    "effective_at": "2026-10-18T00:10:00Z",
                    "metadata": {
                        "tenant_id": "tenant-a",
                        "strategy_id": strategy_id,
                        "last_transition_at": "2026-10-18T00:10:00Z",
                    },
                },
            ),
            single_runtime_enforced=True,
        )
        state["plans"] = {
            plan_id: dict(plan_a, binding_id="synthetic-legal-paused-binding"),
            "plan-same-pool-active": {
                "plan_id": "plan-same-pool-active",
                "strategy_id": strategy_id,
                "capital_pool_id": pool_a,
                "artifact_id": spec_id_v2,
                "artifact_version": "2.0.0",
                "target_stage": "canary",
                "status": "executed",
                "tenant_id": "tenant-a",
                "binding_id": "synthetic-legal-active-binding",
            },
        }
        resp_legal_same_pool = strict_client.get(
            f"/api/registry/strategies/{strategy_id}/deployment-view",
            headers=_bearer(token_a),
        )
        assert resp_legal_same_pool.status_code == 200, resp_legal_same_pool.text
        assert resp_legal_same_pool.json() == {
            "strategy_id": strategy_id,
            "latest_approved_registry_id": spec_id_v2,
            "latest_approved_version": "2.0.0",
            "current_stage": "canary",
            "deployment_plan_id": "plan-same-pool-active",
            "runtime_binding_id": "synthetic-legal-active-binding",
            "last_transition_at": "2026-10-18T00:10:00Z",
        }
        state["use_raw_rm_store"] = False

        # Retirement -> current_stage='none', then rollback/reactivation binding + plan -> canary
        state["bindings"] = [
            {
                **active_binding_a,
                "status": "retired",
                "retired_at": "2026-10-18T01:00:00Z",
            }
        ]
        state["plans"] = {plan_id: dict(plan_a)}
        resp_retired = strict_client.get(
            f"/api/registry/strategies/{strategy_id}/deployment-view",
            headers=_bearer(token_a),
        )
        assert resp_retired.status_code == 200, resp_retired.text
        assert resp_retired.json()["current_stage"] == "none"
        assert resp_retired.json()["deployment_plan_id"] is None
        assert resp_retired.json()["runtime_binding_id"] is None

        rollback_binding = {
            "binding_id": "rb-rollback-v1",
            "capital_pool_id": pool_a,
            "artifact_id": bundle_rid,
            "artifact_version": "1.0.0",
            "deployment_mode": "canary",
            "plan_id": "plan-rollback-v1",
            "status": "active",
            "rollback_parent": binding_id,
            "rollback_action_type": "replace",
            "effective_at": "2026-10-18T01:30:00Z",
            "metadata": {"tenant_id": "tenant-a", "strategy_id": strategy_id},
        }
        rollback_plan = {
            **plan_a,
            "plan_id": "plan-rollback-v1",
            "target_stage": "canary",
            "status": "executed",
            "binding_id": "rb-rollback-v1",
        }
        state["bindings"] = [
            {**active_binding_a, "status": "retired", "retired_at": "2026-10-18T01:00:00Z"},
            rollback_binding,
        ]
        state["plans"] = {**state["plans"], "plan-rollback-v1": rollback_plan}
        resp_rollback = strict_client.get(
            f"/api/registry/strategies/{strategy_id}/deployment-view",
            headers=_bearer(token_a),
        )
        assert resp_rollback.status_code == 200, resp_rollback.text
        assert resp_rollback.json() == {
            "strategy_id": strategy_id,
            "latest_approved_registry_id": spec_id_v2,
            "latest_approved_version": "2.0.0",
            "current_stage": "canary",
            "deployment_plan_id": "plan-rollback-v1",
            "runtime_binding_id": "rb-rollback-v1",
            "last_transition_at": "2026-10-18T01:30:00Z",
        }

        # Separate valid pools: newer paused/pending_pause transition in pool-b wins over older active binding in pool-a
        paused_pool_b_binding = {
            "binding_id": "rb-pool-b-paused",
            "capital_pool_id": pool_b,
            "artifact_id": spec_id_v2,
            "artifact_version": "2.0.0",
            "deployment_mode": "frozen",
            "plan_id": "plan-pool-b",
            "status": "paused",
            "effective_at": "2026-10-18T01:00:00Z",
            "metadata": {
                "tenant_id": "tenant-a",
                "strategy_id": strategy_id,
                "last_transition_at": "2026-10-18T02:00:00Z",
            },
        }
        plan_pool_b = {
            "plan_id": "plan-pool-b",
            "strategy_id": strategy_id,
            "capital_pool_id": pool_b,
            "artifact_id": spec_id_v2,
            "artifact_version": "2.0.0",
            "target_stage": "frozen",
            "status": "executed",
            "tenant_id": "tenant-a",
            "binding_id": "rb-pool-b-paused",
        }
        state["bindings"] = [dict(active_binding_a), paused_pool_b_binding]
        state["plans"] = {plan_id: dict(plan_a), "plan-pool-b": plan_pool_b}
        resp_multi_pool = strict_client.get(
            f"/api/registry/strategies/{strategy_id}/deployment-view",
            headers=_bearer(token_a),
        )
        assert resp_multi_pool.status_code == 200, resp_multi_pool.text
        assert resp_multi_pool.json() == {
            "strategy_id": strategy_id,
            "latest_approved_registry_id": spec_id_v2,
            "latest_approved_version": "2.0.0",
            "current_stage": "frozen",
            "deployment_plan_id": "plan-pool-b",
            "runtime_binding_id": "rb-pool-b-paused",
            "last_transition_at": "2026-10-18T02:00:00Z",
        }

        # Contradictory multiple ACTIVE bindings in the SAME single-runtime-enforced pool -> 503
        state["bindings"] = [
            dict(active_binding_a),
            {**paused_pool_b_binding, "status": "active", "capital_pool_id": pool_a},
        ]
        resp_same_pool_conflict = strict_client.get(
            f"/api/registry/strategies/{strategy_id}/deployment-view",
            headers=_bearer(token_a),
        )
        assert resp_same_pool_conflict.status_code == 503, resp_same_pool_conflict.text

        # Exact binding/entry/plan mismatch cases must all fail closed with 503 (never skip to 'none')
        mismatch_cases = [
            # (f) binding artifact_version mismatch against Registry entry
            ([dict(active_binding_a, artifact_version="9.9.9")], {plan_id: dict(plan_a)}),
            # (f) binding strategy_id mismatch for entry of this strategy
            ([dict(active_binding_a, metadata={"tenant_id": "tenant-a", "strategy_id": "wrong-strat"})], {plan_id: dict(plan_a)}),
            # (f) binding pointing to non-approved (draft) entry of this strategy
            ([dict(active_binding_a, artifact_id=draft_rid, artifact_version="3.0.0")], {plan_id: dict(plan_a)}),
            # (c) DeploymentPlan plan_id mismatch
            ([dict(active_binding_a)], {plan_id: dict(plan_a, plan_id="wrong-plan-id")}),
            # (d) DeploymentPlan capital_pool_id mismatch
            ([dict(active_binding_a)], {plan_id: dict(plan_a, capital_pool_id="wrong-pool")}),
            # (e) canonical DeploymentPlan.binding_id mismatch
            ([dict(active_binding_a)], {plan_id: dict(plan_a, binding_id="rb-wrong-binding")}),
            # (e) executed DeploymentPlan missing binding_id
            ([dict(active_binding_a)], {plan_id: dict(plan_a, status="executed", binding_id=None)}),
            # Stage mismatch: binding deployment_mode='paper' vs plan target_stage='frozen'
            ([dict(active_binding_a, deployment_mode="paper")], {plan_id: dict(plan_a, target_stage="frozen")}),
            # Stage mismatch: binding deployment_mode='frozen' vs plan target_stage='paper'
            ([dict(active_binding_a, deployment_mode="frozen")], {plan_id: dict(plan_a, target_stage="paper")}),
            # Rejected DeploymentPlan
            ([dict(active_binding_a)], {plan_id: dict(plan_a, status="rejected")}),
            # DeploymentPlan missing authoritative tenant_id (owner returns 409)
            ([dict(active_binding_a)], {plan_id: dict(plan_a, tenant_id=None)}),
            # Missing DeploymentPlan (owner returns 404)
            ([dict(active_binding_a)], {}),
        ]
        for bad_bindings, bad_plans in mismatch_cases:
            state["bindings"] = bad_bindings
            state["plans"] = bad_plans
            resp_bad = strict_client.get(
                f"/api/registry/strategies/{strategy_id}/deployment-view",
                headers=_bearer(token_a),
            )
            assert resp_bad.status_code == 503, f"Expected 503 for {bad_bindings} / {bad_plans}, got {resp_bad.status_code}: {resp_bad.text}"


def test_list_all_strategy_spec_entries_tenant_scoped_and_artifact_state_filter(strict_client):
    token_a = _jwt(subject="op-a", tenant="tenant-a")
    token_b = _jwt(subject="op-b", tenant="tenant-b")
    token_admin = _jwt(subject="svc-bff", tenant="tenant-a", roles=["admin"])

    r_a1_v1 = strict_client.post(
        "/api/registry/strategy-specs",
        json={
            "registry_id": "reg-spec-a1-v1",
            "strategy_id": "strat-list-a1",
            "version": "1.0.0",
            "source_seed_id": "seed-a1",
            "strategy_spec": _minimal_strategy_spec("strat-list-a1"),
        },
        headers=_bearer(token_a),
    )
    assert r_a1_v1.status_code == 200, r_a1_v1.text
    for target, expected in (("candidate", "draft"), ("approved", "candidate")):
        adv = advance_registry_http(
            strict_client,
            "/api/registry/strategy-specs/reg-spec-a1-v1/advance",
            json={"target_state": target, "expected_artifact_state": expected},
            headers=_bearer(token_a),
        )
        assert adv.status_code == 200, adv.text

    spec_v2 = _minimal_strategy_spec("strat-list-a1")
    spec_v2["title"] = "Strat List A1 v2"
    r_a1_v2 = strict_client.post(
        "/api/registry/strategy-specs",
        json={
            "registry_id": "reg-spec-a1-v2",
            "strategy_id": "strat-list-a1",
            "version": "1.1.0",
            "artifact_state": "candidate",
            "lineage": {"parent_registry_ids": ["reg-spec-a1-v1"], "source_strategy_spec_id": "reg-spec-a1-v1"},
            "strategy_spec": spec_v2,
        },
        headers=_bearer(token_a),
    )
    assert r_a1_v2.status_code == 200, r_a1_v2.text

    r_b1 = strict_client.post(
        "/api/registry/strategy-specs",
        json={
            "registry_id": "reg-spec-b1-v1",
            "strategy_id": "strat-list-b1",
            "version": "1.0.0",
            "source_seed_id": "seed-b1",
            "strategy_spec": _minimal_strategy_spec("strat-list-b1"),
        },
        headers=_bearer(token_b),
    )
    assert r_b1.status_code == 200, r_b1.text

    r_bundle = strict_client.post(
        "/api/registry/entries",
        json={
            "artifact_type": "execution_bundle",
            "strategy_id": "strat-list-a1",
            "version": "1.0.0",
            "storage_ref": {"backend": "inline", "path": "inline://bundles/b1"},
            "checksum": "sha256:bundle-list-a1",
            "lineage": {"parent_registry_ids": ["reg-spec-a1-v1"], "source_strategy_spec_id": "reg-spec-a1-v1"},
        },
        headers=_bearer(token_a),
    )
    assert r_bundle.status_code == 200, r_bundle.text

    resp_a = strict_client.get(
        "/api/registry/strategy-specs",
        headers={**_bearer(token_a), "X-Tenant-Id": "tenant-a"},
    )
    assert resp_a.status_code == 200, resp_a.text
    ids_a = {item["entry"]["registry_id"] for item in resp_a.json()}
    assert {"reg-spec-a1-v1", "reg-spec-a1-v2"}.issubset(ids_a)
    assert "reg-spec-b1-v1" not in ids_a
    assert all(item["entry"]["artifact_type"] == "strategy_spec" for item in resp_a.json())

    resp_approved = strict_client.get(
        "/api/registry/strategy-specs?artifact_state=approved",
        headers=_bearer(token_a),
    )
    assert resp_approved.status_code == 200, resp_approved.text
    approved_ids = {item["entry"]["registry_id"] for item in resp_approved.json()}
    assert "reg-spec-a1-v1" in approved_ids
    assert "reg-spec-a1-v2" not in approved_ids

    resp_mismatch = strict_client.get(
        "/api/registry/strategy-specs",
        headers={**_bearer(token_a), "X-Tenant-Id": "tenant-b"},
    )
    assert resp_mismatch.status_code == 403, resp_mismatch.text

    resp_admin_mismatch = strict_client.get(
        "/api/registry/strategies/strat-list-a1/strategy-specs",
        headers={**_bearer(token_admin), "X-Tenant-Id": "tenant-b"},
    )
    assert resp_admin_mismatch.status_code == 403, resp_admin_mismatch.text

    resp_unauth = strict_client.get("/api/registry/strategy-specs")
    assert resp_unauth.status_code == 401, resp_unauth.text

    # Auth and X-Tenant-Id validation must execute BEFORE get_registry_service()/store reads on both routes
    import services.registry.service as reg_service_mod

    orig_get_svc = reg_service_mod.get_registry_service
    owner_calls: list[str] = []

    def _forbidden_get_svc():
        owner_calls.append("get_registry_service")
        raise AssertionError("get_registry_service() must not be invoked before auth and X-Tenant-Id validation")

    reg_service_mod.get_registry_service = _forbidden_get_svc
    try:
        for path in ("/api/registry/strategy-specs", "/api/registry/strategies/strat-list-a1/strategy-specs"):
            assert strict_client.get(path).status_code == 401
            assert strict_client.get(path, headers={"Authorization": "Bearer malformed-not-a-jwt"}).status_code == 401
            assert strict_client.get(path, headers={**_bearer(token_a), "X-Tenant-Id": "tenant-b"}).status_code == 403
            assert strict_client.get(path, headers={**_bearer(token_a), "X-Tenant-Id": "__builtin__"}).status_code == 403
            assert strict_client.get(path, headers={**_bearer(token_a), "X-Tenant-Id": "   "}).status_code == 403
            assert strict_client.get(path, headers={**_bearer(token_admin), "X-Tenant-Id": "tenant-b"}).status_code == 403
        assert owner_calls == []
    finally:
        reg_service_mod.get_registry_service = orig_get_svc





def test_strategy_spec_list_persona_binding_view_is_light_and_scoped(strict_client):
    token_a = _jwt(subject="pb-a", tenant="tenant-a")
    token_b = _jwt(subject="pb-b", tenant="tenant-b")
    first_ids: dict[str, str] = {}
    for token, sid, version, personas in (
        (token_a, "pb-strat-a", "1.0.0", ["p1"]),
        (token_a, "pb-strat-a", "1.1.0", ["p1", "p2"]),
        (token_b, "pb-strat-b", "1.0.0", ["p9"]),
    ):
        lineage = {"source_run_ids": ["run-1"]}
        if sid in first_ids:
            lineage["parent_registry_ids"] = [first_ids[sid]]
        resp = strict_client.post(
            "/api/registry/strategy-specs",
            json={
                "strategy_id": sid,
                "version": version,
                "lineage": lineage,
                "strategy_spec": _valid_spec(sid, bulk="x" * 5000, rev=version),
                "metadata": {"persona_ids": personas, "tenant_id": "ignored"},
            },
            headers=_bearer(token),
        )
        assert resp.status_code == 200, resp.text
        first_ids.setdefault(sid, resp.json()["entry"]["registry_id"])

    path = "/api/registry/strategy-specs"
    full = strict_client.get(path, headers=_bearer(token_a))
    light = strict_client.get(f"{path}?view=persona_binding", headers=_bearer(token_a))
    assert full.status_code == light.status_code == 200
    full_by_id = {e["entry"]["registry_id"]: e["entry"] for e in full.json()}
    light_by_id = {e["entry"]["registry_id"]: e["entry"] for e in light.json()}
    assert set(light_by_id) == set(full_by_id)  # same rows, all versions, tenant-scoped
    assert not any(e["strategy_id"] == "pb-strat-b" for e in light_by_id.values())
    for rid, le in light_by_id.items():
        fe = full_by_id[rid]
        for key in ("strategy_id", "version", "artifact_state", "owner_tenant", "created_at"):
            assert le[key] == fe[key]
        assert le["metadata"].get("persona_ids") == fe["metadata"].get("persona_ids")
        assert "bulk" not in str(le["metadata"])
    assert len(light.text) < len(full.text) / 3

    # Unauthenticated and bad view are rejected, never silently full/empty.
    assert strict_client.get(f"{path}?view=persona_binding").status_code in (401, 403)
    assert strict_client.get(f"{path}?view=bogus", headers=_bearer(token_a)).status_code == 422


def test_strategy_spec_persona_binding_view_keeps_whole_bound_families_only(strict_client):
    token_a = _jwt(subject="pbf-a", tenant="tenant-a")
    token_b = _jwt(subject="pbf-b", tenant="tenant-b")
    first_ids: dict[tuple[str, str], str] = {}
    for token, tenant, sid, version, personas in (
        (token_a, "a", "pbf-bound-old", "1.0.0", ["p1"]),
        (token_a, "a", "pbf-bound-old", "2.0.0", []),
        (token_a, "a", "pbf-unbound", "1.0.0", []),
        (token_b, "b", "pbf-other-tenant", "1.0.0", ["p9"]),
    ):
        lineage = {"source_run_ids": ["run-1"]}
        if (tenant, sid) in first_ids:
            lineage["parent_registry_ids"] = [first_ids[(tenant, sid)]]
        resp = strict_client.post(
            "/api/registry/strategy-specs",
            json={
                "strategy_id": sid, "version": version, "lineage": lineage,
                "strategy_spec": _valid_spec(sid, rev=version),
                "metadata": {"persona_ids": personas},
            },
            headers=_bearer(token),
        )
        assert resp.status_code == 200, resp.text
        first_ids.setdefault((tenant, sid), resp.json()["entry"]["registry_id"])

    path = "/api/registry/strategy-specs"
    light = strict_client.get(f"{path}?view=persona_binding", headers=_bearer(token_a))
    assert light.status_code == 200
    got = sorted((e["entry"]["strategy_id"], e["entry"]["version"]) for e in light.json())
    # Every version of the bound family (incl. unbound latest) is kept; the fully
    # unbound family is dropped; tenant-b's bound family never leaks into tenant-a.
    assert got == [("pbf-bound-old", "1.0.0"), ("pbf-bound-old", "2.0.0")]
