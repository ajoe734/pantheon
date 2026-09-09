"""Registry admits a paper-scoped approval only for a validated paper candidate."""
from __future__ import annotations

import copy
import uuid

import pytest

from services.governance.paper_approval_scope import DEV_PAPER_AUTHORIZATION_SCOPE, _canonical_sha256
from services.governance.test_approval_authority import (
    SnapshotApprovalReader, advance_registry_unit, approval_snapshot, configure_registry_unit_auth,
)
from services.registry.paper_strategy_spec import build_strategy_spec

from .models import ArtifactState, ArtifactType, Lineage, RegistryEntryCreate, StorageBackend, StorageRef
from .split_api import RegistryError, RegistryService
from .storage import RegistryStore, reset_store

TENANT, PERSONA, POOL = "tenant-unit", "persona-paper-001", "pool-paper-001"
ACTOR = {"id": "unit-operator", "tenant": TENANT}


@pytest.fixture(autouse=True)
def clean_store(monkeypatch):
    configure_registry_unit_auth(monkeypatch)
    reset_store()
    yield
    reset_store()


def _embedded_metadata(**overrides):
    values = {"tenant_id": TENANT, "persona_id": PERSONA, "capital_pool_id": POOL,
              "execution_context": "paper", "capital_scale_pct": 0}
    values.update(overrides)
    return values


def _paper_metadata(spec):
    return {"tenant_id": TENANT, "execution_context": "paper", "capital_scale_pct": 0, "persona_id": PERSONA,
            "capital_pool_id": POOL, "strategy_spec": spec}


def _register_spec(svc, registry_id, *, metadata=None, embedded=None):
    spec = build_strategy_spec()
    spec["metadata"] = _embedded_metadata() if embedded is None else embedded
    payload = RegistryEntryCreate(
        artifact_type=ArtifactType.STRATEGY_SPEC, strategy_id=spec["strategy_id"], version="1.0.0",
        artifact_state=ArtifactState.DRAFT, lineage=Lineage(source_run_ids=["paper-proof-run"]),
        storage_ref=StorageRef(backend=StorageBackend.INLINE, path="$.entry.metadata.strategy_spec"),
        checksum=_canonical_sha256(spec),
        metadata=_paper_metadata(spec) if metadata is None else metadata,
    )
    svc.register(payload, registry_id, actor=ACTOR)
    return advance_registry_unit(svc, registry_id, ArtifactState.CANDIDATE).entry


def _approve(svc, entry, *, scoped=True, evidence_overrides=None):
    values = dict(
        decision_id="approval-" + entry.registry_id, tenant_id=entry.owner_tenant, target_id=entry.registry_id,
        target_version=entry.version, candidate_digest=entry.checksum, persona_id=PERSONA, capital_pool_id=POOL,
        actor_role="automated_gate", actor_id="pantheon-dev-paper-provisioner",
    )
    if scoped:
        values["authorization_scope"] = copy.deepcopy(DEV_PAPER_AUTHORIZATION_SCOPE)
    values.update(evidence_overrides or {})
    snapshot = approval_snapshot(**values)
    svc.approval_reader = SnapshotApprovalReader(snapshot)
    return svc.advance_artifact_state(
        entry.registry_id, ArtifactState.APPROVED, approval_decision_id=snapshot["decision_id"],
        command_key=uuid.uuid4().hex, actor=ACTOR, expected_artifact_state=entry.artifact_state,
        expected_version=entry.version, expected_updated_at=entry.updated_at,
    )


def test_scoped_approval_admits_validated_paper_candidate_in_dev(monkeypatch):
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    svc = RegistryService(RegistryStore())
    entry = _register_spec(svc, "reg-paper-spec-001")
    approved = _approve(svc, entry).entry
    assert approved.artifact_state == ArtifactState.APPROVED
    assert approved.approval_evidence["authorization_scope"] == DEV_PAPER_AUTHORIZATION_SCOPE
    assert approved.approver == "pantheon-dev-paper-provisioner"


@pytest.mark.parametrize("environment", [None, "prod"])
def test_scoped_approval_fails_closed_outside_dev(monkeypatch, environment):
    if environment is None:
        monkeypatch.delenv("PANTHEON_ENV", raising=False)
    else:
        monkeypatch.setenv("PANTHEON_ENV", environment)
    svc = RegistryService(RegistryStore())
    entry = _register_spec(svc, "reg-paper-spec-002")
    with pytest.raises(RegistryError, match="authorization_scope"):
        _approve(svc, entry)
    assert svc.get(entry.registry_id).entry.artifact_state == ArtifactState.CANDIDATE


@pytest.mark.parametrize("metadata", [
    None,
    lambda spec: {"strategy_spec": spec},
    lambda spec: {**_paper_metadata(spec), "capital_scale_pct": 1},
    lambda spec: {**_paper_metadata(spec), "execution_context": "live"},
    lambda spec: {**_paper_metadata(spec), "persona_id": "persona-other"},
    lambda spec: {**_paper_metadata(spec), "capital_pool_id": "pool-other"},
])
def test_scoped_approval_never_admits_non_paper_candidate(monkeypatch, metadata):
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    svc = RegistryService(RegistryStore())
    spec = build_strategy_spec()
    entry = _register_spec(svc, "reg-paper-spec-003", metadata={} if metadata is None else metadata(spec))
    with pytest.raises(RegistryError):
        _approve(svc, entry)
    assert svc.get(entry.registry_id).entry.artifact_state == ArtifactState.CANDIDATE


def test_scoped_approval_with_mismatched_persona_evidence_is_denied(monkeypatch):
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    svc = RegistryService(RegistryStore())
    entry = _register_spec(svc, "reg-paper-spec-004")
    with pytest.raises(RegistryError):
        _approve(svc, entry, evidence_overrides={"persona_id": "persona-other"})
    assert svc.get(entry.registry_id).entry.artifact_state == ArtifactState.CANDIDATE


@pytest.mark.parametrize("embedded", [
    _embedded_metadata(persona_id="persona-other"), _embedded_metadata(capital_pool_id="pool-other"),
    _embedded_metadata(tenant_id="tenant-other"), _embedded_metadata(capital_scale_pct=1),
])
def test_scoped_approval_denies_embedded_spec_drift_with_consistent_checksum(monkeypatch, embedded):
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    svc = RegistryService(RegistryStore())
    entry = _register_spec(svc, "reg-paper-spec-005", embedded=embedded)
    assert entry.metadata["persona_id"] == PERSONA  # outer metadata is correct; digest is consistent
    with pytest.raises(RegistryError, match="strategy_spec.metadata"):
        _approve(svc, entry)
    assert svc.get(entry.registry_id).entry.artifact_state == ArtifactState.CANDIDATE


def test_dedicated_subject_without_scope_is_never_legacy_authority(monkeypatch):
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    svc = RegistryService(RegistryStore())
    entry = _register_spec(svc, "reg-paper-spec-006")
    with pytest.raises(RegistryError, match="authorization_scope"):
        _approve(svc, entry, scoped=False)
    with pytest.raises(RegistryError, match="authorization_scope"):
        _approve(svc, entry, scoped=False, evidence_overrides={"actor_id": "unit-reviewer", "actor_role": "governance_reviewer",
                                                               "owner_user_id": "pantheon-dev-paper-provisioner"})
    assert svc.get(entry.registry_id).entry.artifact_state == ArtifactState.CANDIDATE


def test_unscoped_generic_approval_keeps_existing_registry_behaviour(monkeypatch):
    monkeypatch.delenv("PANTHEON_ENV", raising=False)
    svc = RegistryService(RegistryStore())
    entry = _register_spec(svc, "reg-legacy-spec-001", metadata={})
    approved = _approve(svc, entry, scoped=False,
                        evidence_overrides={"actor_id": "unit-reviewer", "actor_role": "governance_reviewer"}).entry
    assert approved.artifact_state == ArtifactState.APPROVED
    assert approved.approval_evidence["authorization_scope"] is None
