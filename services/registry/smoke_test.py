"""
BP5-SVC-002: Pure-Python smoke tests for the split artifact_state / deployment_stage model.

Runs with `python3 services/registry/smoke_test.py` — no pytest, no httpx, no pip required.
Tests the core models, storage, and split_api logic directly.
"""
from __future__ import annotations

import sys
import os

# Ensure repo-root imports work when this file is executed directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.governance.test_approval_authority import advance_registry_unit
from services.registry.models import (
    ALLOWED_ARTIFACT_TRANSITIONS,
    ArtifactState,
    DeploymentStage,
    DeploymentSummary,
    Lineage,
    RegistryEntryCreate,
    StorageBackend,
    StorageRef,
)
from services.registry.split_api import RegistryError, RegistryOwnerUnavailableError, RegistryService
from services.registry.storage import RegistryStore, reset_store, get_store

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_passed = 0
_failed = 0
_SMOKE_ACTOR = {"id": "unit-operator", "tenant": "tenant-unit"}


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


def assert_eq(actual, expected, msg=""):
    global _passed, _failed
    if actual != expected:
        _failed += 1
        raise AssertionError(f"FAIL: {msg or 'assertion'} — expected {expected!r}, got {actual!r}")
    _passed += 1


def assert_is_none(val, msg=""):
    global _passed, _failed
    if val is not None:
        _failed += 1
        raise AssertionError(f"FAIL: {msg or 'assertion'} — expected None, got {val!r}")
    _passed += 1


def assert_is_not_none(val, msg=""):
    global _passed, _failed
    if val is None:
        _failed += 1
        raise AssertionError(f"FAIL: {msg or 'assertion'} — expected non-None")
    _passed += 1


def assert_raises(exc_type, fn, msg=""):
    global _passed, _failed
    try:
        fn()
        _failed += 1
        raise AssertionError(f"FAIL: {msg or 'assertion'} — expected {exc_type.__name__} but no exception raised")
    except exc_type:
        _passed += 1


def reset():
    reset_store()


# ---------------------------------------------------------------------------
# Tests: Artifact-state transitions
# ---------------------------------------------------------------------------

def test_draft_to_candidate():
    reset()
    store = RegistryStore()
    svc = RegistryService(store)
    svc.register(_make_create_payload(), "reg-001", actor=_SMOKE_ACTOR)
    advanced = advance_registry_unit(svc, "reg-001", ArtifactState.CANDIDATE)
    assert_eq(advanced.entry.artifact_state, ArtifactState.CANDIDATE, "draft->candidate")


def test_candidate_to_approved():
    reset()
    store = RegistryStore()
    svc = RegistryService(store)
    svc.register(_make_create_payload(), "reg-001", actor=_SMOKE_ACTOR)
    advance_registry_unit(svc, "reg-001", ArtifactState.CANDIDATE)
    advanced = advance_registry_unit(
        svc,
        "reg-001",
        ArtifactState.APPROVED,
        approver="risk-committee",
        approval_decision_id="decision-reg-001",
    )
    assert_eq(advanced.entry.artifact_state, ArtifactState.APPROVED, "candidate->approved")
    assert_eq(advanced.entry.approver, "risk-committee", "approver set")
    assert_is_not_none(advanced.entry.approved_at, "approved_at set")


def test_approval_requires_lineage():
    reset()
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
        actor=_SMOKE_ACTOR,
    )
    advance_registry_unit(svc, "reg-001", ArtifactState.CANDIDATE)
    assert_raises(
        RegistryError,
        lambda: advance_registry_unit(svc, "reg-001", ArtifactState.APPROVED),
        "candidate without lineage cannot be approved",
    )


def test_approved_to_retired():
    reset()
    store = RegistryStore()
    svc = RegistryService(store)
    svc.register(_make_create_payload(), "reg-001", actor=_SMOKE_ACTOR)
    advance_registry_unit(svc, "reg-001", ArtifactState.CANDIDATE)
    advance_registry_unit(svc, "reg-001", ArtifactState.APPROVED)
    advanced = advance_registry_unit(svc, "reg-001", ArtifactState.RETIRED)
    assert_eq(advanced.entry.artifact_state, ArtifactState.RETIRED, "approved->retired")


def test_draft_to_retired():
    reset()
    store = RegistryStore()
    svc = RegistryService(store)
    svc.register(_make_create_payload(), "reg-001", actor=_SMOKE_ACTOR)
    advanced = advance_registry_unit(svc, "reg-001", ArtifactState.RETIRED)
    assert_eq(advanced.entry.artifact_state, ArtifactState.RETIRED, "draft->retired")


def test_candidate_to_retired():
    reset()
    store = RegistryStore()
    svc = RegistryService(store)
    svc.register(_make_create_payload(), "reg-001", actor=_SMOKE_ACTOR)
    advance_registry_unit(svc, "reg-001", ArtifactState.CANDIDATE)
    advanced = advance_registry_unit(svc, "reg-001", ArtifactState.RETIRED)
    assert_eq(advanced.entry.artifact_state, ArtifactState.RETIRED, "candidate->retired")


def test_forbidden_draft_to_approved():
    reset()
    store = RegistryStore()
    svc = RegistryService(store)
    svc.register(_make_create_payload(), "reg-001", actor=_SMOKE_ACTOR)
    assert_raises(
        RegistryError,
        lambda: advance_registry_unit(svc, "reg-001", ArtifactState.APPROVED),
        "draft->approved forbidden",
    )


def test_forbidden_retired_to_draft():
    reset()
    store = RegistryStore()
    svc = RegistryService(store)
    svc.register(_make_create_payload(), "reg-001", actor=_SMOKE_ACTOR)
    advance_registry_unit(svc, "reg-001", ArtifactState.RETIRED)
    assert_raises(
        RegistryError,
        lambda: advance_registry_unit(svc, "reg-001", ArtifactState.DRAFT),
        "retired->draft forbidden",
    )


def test_approved_does_not_change_deployment_stage():
    reset()
    store = RegistryStore()
    svc = RegistryService(store)
    svc.register(_make_create_payload(), "reg-001", actor=_SMOKE_ACTOR)
    advance_registry_unit(svc, "reg-001", ArtifactState.CANDIDATE)
    approved = advance_registry_unit(svc, "reg-001", ArtifactState.APPROVED)
    assert_eq(approved.deployment_stage, DeploymentStage.NONE, "approved does not set deployment_stage")


# ---------------------------------------------------------------------------
# Tests: Storage
# ---------------------------------------------------------------------------

def test_create_and_get():
    reset()
    store = RegistryStore()
    store.create(_make_create_payload(), "reg-001")
    got = store.get("reg-001")
    assert_is_not_none(got, "entry exists")
    assert_eq(got.registry_id, "reg-001", "registry_id matches")


def test_get_not_found():
    reset()
    store = RegistryStore()
    assert_is_none(store.get("nonexistent"), "not found returns None")


def test_list_by_strategy():
    reset()
    store = RegistryStore()
    store.create(_make_create_payload(version="1.0.0"), "reg-001")
    store.create(_make_create_payload(version="1.1.0"), "reg-002")
    store.create(_make_create_payload(strategy_id="other", version="1.0.0"), "reg-003")
    entries = store.list_by_strategy("test-alpha")
    assert_eq(len(entries), 2, "list_by_strategy count")


def test_resolve_latest_approved_none():
    reset()
    store = RegistryStore()
    store.create(_make_create_payload(), "reg-001")  # draft
    assert_is_none(store.resolve_latest_approved("test-alpha"), "no approved returns None")


def test_resolve_latest_approved_semver():
    reset()
    store = RegistryStore()
    for rid, ver in [("reg-001", "1.0.0"), ("reg-002", "2.0.0"), ("reg-003", "1.5.0")]:
        store.create(_make_create_payload(version=ver), rid)
        entry = store.get(rid)
        entry.artifact_state = ArtifactState.APPROVED
        store.update(entry)

    latest = store.resolve_latest_approved("test-alpha")
    assert_is_not_none(latest, "latest approved exists")
    assert_eq(latest.version, "2.0.0", "latest approved version is 2.0.0")


def test_deployment_summary_update():
    reset()
    store = RegistryStore()
    store.create(_make_create_payload(), "reg-001")
    updated = store.update_deployment_summary(
        "reg-001",
        current_stage=DeploymentStage.PAPER,
        deployment_plan_id="plan-001",
    )
    assert_is_not_none(updated, "update returns entry")
    assert_is_not_none(updated.deployment_summary, "deployment_summary set")
    assert_eq(updated.deployment_summary.current_stage, DeploymentStage.PAPER, "stage is paper")
    assert_eq(updated.deployment_summary.deployment_plan_id, "plan-001", "plan_id set")


# ---------------------------------------------------------------------------
# Tests: Deployment view (owner-composed)
# ---------------------------------------------------------------------------

def test_empty_strategy():
    reset()
    store = RegistryStore()
    svc = RegistryService(store)
    assert_raises(
        RegistryOwnerUnavailableError,
        lambda: svc.resolve_deployment_view("nonexistent"),
        "missing owner configuration fails closed",
    )
    view = svc.resolve_deployment_view(
        "nonexistent",
        runtime_bindings_reader=lambda _tid: [],
        deployment_plan_reader=lambda _pid, _tid: None,
    )
    assert_eq(view.current_stage, DeploymentStage.NONE, "empty strategy has NONE stage")


def test_approved_without_deployment_summary():
    reset()
    store = RegistryStore()
    svc = RegistryService(store)
    svc.register(_make_create_payload(), "reg-001", actor=_SMOKE_ACTOR)
    advance_registry_unit(svc, "reg-001", ArtifactState.CANDIDATE)
    advance_registry_unit(svc, "reg-001", ArtifactState.APPROVED)
    # Even if non-authoritative deployment_summary is set on the entry, resolve_deployment_view
    # must never read it and must report NONE when no canonical RuntimeBinding is active.
    svc.update_deployment_summary(
        "reg-001",
        current_stage=DeploymentStage.PAPER,
        deployment_plan_id="cached-plan",
        runtime_binding_id="cached-rb",
    )

    view = svc.resolve_deployment_view(
        "test-alpha",
        tenant_id="tenant-unit",
        runtime_bindings_reader=lambda _tid: [],
        deployment_plan_reader=lambda _pid, _tid: None,
    )
    assert_eq(view.current_stage, DeploymentStage.NONE, "no active RuntimeBinding → NONE")
    assert_eq(view.latest_approved_registry_id, "reg-001", "latest_approved_registry_id set")


def test_approved_with_deployment_summary():
    reset()
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
    svc.register(_make_create_payload(), "reg-001", actor=_SMOKE_ACTOR)
    advance_registry_unit(svc, "reg-001", ArtifactState.CANDIDATE)
    advance_registry_unit(svc, "reg-001", ArtifactState.APPROVED)

    view = svc.resolve_deployment_view("test-alpha", tenant_id="tenant-unit")
    assert_eq(view.current_stage, DeploymentStage.CANARY, "stage is canary from owner binding+plan")
    assert_eq(view.deployment_plan_id, "plan-001", "plan_id is plan-001")
    assert_eq(view.runtime_binding_id, "rb-001", "runtime_binding_id is rb-001")
    assert_eq(view.last_transition_at, "2026-10-18T00:00:00Z", "last_transition_at from owner binding")


# ---------------------------------------------------------------------------
# Test: Split semantics (key acceptance criterion)
# ---------------------------------------------------------------------------

def test_split_semantics_approved_has_no_stage():
    """
    Key acceptance test: approving an artifact does NOT assign a deployment stage.
    The artifact is approved, deployment_stage remains none until deployment service acts.
    """
    reset()
    store = RegistryStore()
    svc = RegistryService(store)
    svc.register(_make_create_payload(), "reg-001", actor=_SMOKE_ACTOR)
    advance_registry_unit(svc, "reg-001", ArtifactState.CANDIDATE)
    approved = advance_registry_unit(svc, "reg-001", ArtifactState.APPROVED, approver="test")
    assert_eq(approved.entry.artifact_state, ArtifactState.APPROVED, "artifact_state is approved")
    assert_eq(approved.deployment_stage, DeploymentStage.NONE, "deployment_stage still none")


def test_deployment_stage_updated_independently():
    """
    Deployment stage can be updated independently of artifact_state.
    This validates the split model.
    """
    reset()
    store = RegistryStore()
    svc = RegistryService(store)
    svc.register(_make_create_payload(), "reg-001", actor=_SMOKE_ACTOR)
    advance_registry_unit(svc, "reg-001", ArtifactState.CANDIDATE)
    advance_registry_unit(svc, "reg-001", ArtifactState.APPROVED)

    # Update deployment_stage to paper (simulating deployment service)
    svc.update_deployment_summary("reg-001", current_stage=DeploymentStage.PAPER, deployment_plan_id="plan-001")
    view = svc.get("reg-001")
    assert_eq(view.entry.artifact_state, ArtifactState.APPROVED, "artifact_state still approved")
    assert_eq(view.deployment_stage, DeploymentStage.PAPER, "deployment_stage is paper")


def test_deployment_stage_requires_approved_artifact():
    reset()
    store = RegistryStore()
    svc = RegistryService(store)
    svc.register(_make_create_payload(), "reg-001")
    assert_raises(
        RegistryError,
        lambda: svc.update_deployment_summary("reg-001", current_stage=DeploymentStage.PAPER),
        "draft artifact cannot receive deployment stage",
    )


def test_not_found_raises():
    reset()
    store = RegistryStore()
    svc = RegistryService(store)
    assert_raises(RegistryError, lambda: svc.get("nonexistent"), "get raises RegistryError for missing entry")


# ---------------------------------------------------------------------------
# Tests: register() state-gate (BP5-SVC-002 review fix)
# ---------------------------------------------------------------------------

def test_register_rejects_approved_state():
    """register() must refuse artifact_state=approved — bypasses the state machine."""
    reset()
    store = RegistryStore()
    svc = RegistryService(store)
    payload = RegistryEntryCreate(
        artifact_type="model_artifact",
        strategy_id="strat",
        version="1.0.0",
        artifact_state=ArtifactState.APPROVED,
        lineage=Lineage(source_run_ids=["run"]),
        storage_ref=StorageRef(backend=StorageBackend.OBJECT_STORE, path="x"),
        checksum="c",
    )
    assert_raises(
        RegistryError,
        lambda: svc.register(payload, "reg1"),
        "register() must reject approved state",
    )


def test_register_rejects_retired_state():
    """register() must refuse artifact_state=retired — bypasses the state machine."""
    reset()
    store = RegistryStore()
    svc = RegistryService(store)
    payload = RegistryEntryCreate(
        artifact_type="model_artifact",
        strategy_id="strat",
        version="1.0.0",
        artifact_state=ArtifactState.RETIRED,
        lineage=Lineage(source_run_ids=["run"]),
        storage_ref=StorageRef(backend=StorageBackend.OBJECT_STORE, path="x"),
        checksum="c",
    )
    assert_raises(
        RegistryError,
        lambda: svc.register(payload, "reg1"),
        "register() must reject retired state",
    )


def test_deployment_view_reports_actually_deployed_not_latest_approved():
    """
    resolve_deployment_view() must return the deployment stage of the actually deployed
    entry from canonical RuntimeBinding + DeploymentPlan owners, while reporting the
    newest approved Registry version in latest_approved_*.

    Scenario: 1.0.0 (reg1) has an active live RuntimeBinding + DeploymentPlan;
    2.0.0 (reg2) is approved in Registry but not yet deployed.
    Expected: current_stage=live from 1.0.0 owner binding/plan, latest_approved_version=2.0.0.
    """
    reset()
    store = RegistryStore()
    svc = RegistryService(
        store,
        runtime_bindings_reader=lambda _tid: [
            {
                "binding_id": "rb-live-1",
                "capital_pool_id": "pool-live-1",
                "artifact_id": "reg1",
                "artifact_version": "1.0.0",
                "deployment_mode": "live",
                "plan_id": "plan1",
                "status": "active",
                "effective_at": "2026-10-18T01:00:00Z",
                "metadata": {"tenant_id": "tenant-unit", "strategy_id": "strat"},
            }
        ],
        deployment_plan_reader=lambda pid, _tid: {
            "plan_id": pid,
            "strategy_id": "strat",
            "capital_pool_id": "pool-live-1",
            "artifact_id": "reg1",
            "artifact_version": "1.0.0",
            "target_stage": "live",
            "status": "approved",
            "tenant_id": "tenant-unit",
            "binding_id": "rb-live-1",
        },
    )

    mk = lambda v: RegistryEntryCreate(
        artifact_type="model_artifact",
        strategy_id="strat",
        version=v,
        lineage=Lineage(source_run_ids=["run"]),
        storage_ref=StorageRef(backend=StorageBackend.OBJECT_STORE, path="x"),
        checksum="c",
    )

    # Register and approve 1.0.0
    svc.register(mk("1.0.0"), "reg1", actor=_SMOKE_ACTOR)
    advance_registry_unit(svc, "reg1", ArtifactState.CANDIDATE)
    advance_registry_unit(svc, "reg1", ArtifactState.APPROVED)

    # Register and approve 2.0.0 but do NOT deploy it
    svc.register(mk("2.0.0"), "reg2", actor=_SMOKE_ACTOR)
    advance_registry_unit(svc, "reg2", ArtifactState.CANDIDATE)
    advance_registry_unit(svc, "reg2", ArtifactState.APPROVED)

    view = svc.resolve_deployment_view("strat", tenant_id="tenant-unit")
    assert_eq(view.latest_approved_version, "2.0.0", "latest_approved_version should be the newest approved")
    assert_eq(view.latest_approved_registry_id, "reg2", "latest_approved_registry_id should be reg2")
    assert_eq(view.current_stage, DeploymentStage.LIVE, "current_stage must reflect the active RuntimeBinding (1.0.0 live)")
    assert_eq(view.deployment_plan_id, "plan1", "deployment_plan_id from the linked DeploymentPlan")
    assert_eq(view.runtime_binding_id, "rb-live-1", "runtime_binding_id from the active RuntimeBinding")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

TESTS = [
    test_draft_to_candidate,
    test_candidate_to_approved,
    test_approval_requires_lineage,
    test_approved_to_retired,
    test_draft_to_retired,
    test_candidate_to_retired,
    test_forbidden_draft_to_approved,
    test_forbidden_retired_to_draft,
    test_approved_does_not_change_deployment_stage,
    test_create_and_get,
    test_get_not_found,
    test_list_by_strategy,
    test_resolve_latest_approved_none,
    test_resolve_latest_approved_semver,
    test_deployment_summary_update,
    test_empty_strategy,
    test_approved_without_deployment_summary,
    test_approved_with_deployment_summary,
    test_split_semantics_approved_has_no_stage,
    test_deployment_stage_updated_independently,
    test_deployment_stage_requires_approved_artifact,
    test_not_found_raises,
    test_register_rejects_approved_state,
    test_register_rejects_retired_state,
    test_deployment_view_reports_actually_deployed_not_latest_approved,
]


def main():
    global _passed, _failed
    for fn in TESTS:
        name = fn.__name__
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            print(f"  FAIL  {name}: {e}")
        except Exception as e:
            _failed += 1
            print(f"  ERROR {name}: {type(e).__name__}: {e}")

    total = _passed + _failed
    print(f"\n{'='*60}")
    print(f"Results: {_passed} passed, {_failed} failed, {total} total")
    if _failed > 0:
        print("SMOKE TEST FAILED")
        sys.exit(1)
    else:
        print("ALL SMOKE TESTS PASSED")
        sys.exit(0)


if __name__ == "__main__":
    main()
