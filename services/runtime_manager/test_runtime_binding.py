"""
RT-001: RuntimeBinding schema pytest suite.

Covers the canonical RuntimeBinding domain object, validate_binding(), and
RuntimeBindingStore write-authority rules as specified in contract.md and SD-P0-03.

Run:
    python3 -m pytest services/execution/runtime-manager/test_runtime_binding.py -q
"""
from __future__ import annotations

import importlib.util as _ilu
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

from services.runtime_manager.runtime_binding import (
    DeploymentMode,
    RollbackActionType,
    RuntimeBinding,
    RuntimeBindingError,
    RuntimeBindingStatus,
    RuntimeBindingStore,
    validate_binding,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _base(**overrides: Any) -> RuntimeBinding:
    kwargs: Dict[str, Any] = dict(
        binding_id="rtb-aaa000000001",
        runtime_id="rt-lean-001",
        capital_pool_id="pool-alpha",
        artifact_id="art-strat-001",
        artifact_version="1.0.0",
        deployment_mode=DeploymentMode.PAPER.value,
        effective_at="2026-05-16T00:00:00Z",
        status=RuntimeBindingStatus.ACTIVE.value,
        plan_id="plan-001",
        persona_capital_binding_id="pcb-001",
    )
    kwargs.update(overrides)
    return RuntimeBinding(**kwargs)


@pytest.fixture()
def store() -> RuntimeBindingStore:
    return RuntimeBindingStore()


@pytest.fixture()
def persisted_store(tmp_path: Path) -> RuntimeBindingStore:
    return RuntimeBindingStore(path=tmp_path / "bindings.json")


# ---------------------------------------------------------------------------
# Schema / field tests
# ---------------------------------------------------------------------------

class TestRuntimeBindingSchema:

    def test_valid_creation_required_fields(self) -> None:
        b = _base()
        assert b.binding_id == "rtb-aaa000000001"
        assert b.plan_id == "plan-001"
        assert b.persona_capital_binding_id == "pcb-001"
        assert b.deployment_mode == "paper"
        assert b.execution_mode == "paper"
        assert b.status == "active"
        assert not b.is_terminal()

    def test_validate_binding_returns_no_errors_for_valid(self) -> None:
        assert validate_binding(_base()) == []

    def test_to_dict_round_trip(self) -> None:
        b = _base()
        d = b.to_dict()
        b2 = RuntimeBinding.from_dict(d)
        assert b2.binding_id == b.binding_id
        assert b2.plan_id == b.plan_id
        assert b2.deployment_mode == b.deployment_mode
        assert b2.execution_mode == b.execution_mode

    def test_to_json_round_trip(self) -> None:
        b = _base()
        json_str = b.to_json()
        b2 = RuntimeBinding.from_json(json_str)
        assert b2.binding_id == b.binding_id
        assert b2.capital_pool_id == b.capital_pool_id

    def test_optional_fields_absent_by_default(self) -> None:
        b = _base()
        assert b.retired_at is None
        assert b.rollback_parent is None
        assert b.rollback_action_type is None
        assert b.metadata == {}


# ---------------------------------------------------------------------------
# Required cross-object references (contract.md §4 / INV-CTX)
# ---------------------------------------------------------------------------

class TestRequiredCrossObjectRefs:

    def test_missing_plan_id_caught_by_validate(self) -> None:
        b = _base(plan_id="")
        errors = validate_binding(b)
        assert any("plan_id" in e for e in errors)

    def test_missing_persona_capital_binding_id_caught(self) -> None:
        b = _base(persona_capital_binding_id="")
        errors = validate_binding(b)
        assert any("persona_capital_binding_id" in e for e in errors)

    def test_missing_binding_id_caught(self) -> None:
        b = _base(binding_id="")
        errors = validate_binding(b)
        assert any("binding_id" in e for e in errors)

    def test_missing_runtime_id_caught(self) -> None:
        b = _base(runtime_id="")
        errors = validate_binding(b)
        assert any("runtime_id" in e for e in errors)

    def test_missing_capital_pool_id_caught(self) -> None:
        b = _base(capital_pool_id="")
        errors = validate_binding(b)
        assert any("capital_pool_id" in e for e in errors)

    def test_missing_artifact_id_caught(self) -> None:
        b = _base(artifact_id="")
        errors = validate_binding(b)
        assert any("artifact_id" in e for e in errors)


# ---------------------------------------------------------------------------
# DeploymentMode enum validation
# ---------------------------------------------------------------------------

class TestDeploymentMode:

    def test_all_valid_modes_accepted(self) -> None:
        for mode in DeploymentMode:
            b = _base(deployment_mode=mode.value)
            assert b.deployment_mode == mode.value

    def test_invalid_mode_raises_runtime_binding_error(self) -> None:
        with pytest.raises(RuntimeBindingError):
            _base(deployment_mode="staging")

    def test_deployment_mode_paper(self) -> None:
        b = _base(deployment_mode="paper")
        assert b.deployment_mode == "paper"

    def test_deployment_mode_canary(self) -> None:
        b = _base(deployment_mode="canary")
        assert b.deployment_mode == "canary"
        assert b.execution_mode == "canary"

    def test_deployment_mode_live(self) -> None:
        b = _base(deployment_mode="live")
        assert b.deployment_mode == "live"

    def test_deployment_mode_frozen(self) -> None:
        b = _base(deployment_mode="frozen")
        assert b.deployment_mode == "frozen"

    def test_execution_mode_defaults_to_deployment_mode_for_legacy_records(self) -> None:
        b = RuntimeBinding.from_dict(_base(deployment_mode="canary").to_dict() | {"execution_mode": None})
        assert b.deployment_mode == "canary"
        assert b.execution_mode == "canary"

    def test_execution_mode_must_match_deployment_mode(self) -> None:
        with pytest.raises(RuntimeBindingError, match="execution_mode"):
            _base(deployment_mode="canary", execution_mode="live")


# ---------------------------------------------------------------------------
# Status state machine (contract.md §5)
# ---------------------------------------------------------------------------

class TestStatusLifecycle:

    def test_initial_active_status(self) -> None:
        b = _base()
        assert b.status == "active"
        assert not b.is_terminal()

    def test_active_to_pending_pause(self, store: RuntimeBindingStore) -> None:
        b = _base(binding_id="rtb-001")
        store.create(b, single_runtime_enforced=False)
        updated = store.transition_status("rtb-001", "pending_pause")
        assert updated.status == "pending_pause"

    def test_pending_pause_to_paused(self, store: RuntimeBindingStore) -> None:
        b = _base(binding_id="rtb-001")
        store.create(b, single_runtime_enforced=False)
        store.transition_status("rtb-001", "pending_pause")
        updated = store.transition_status("rtb-001", "paused")
        assert updated.status == "paused"

    def test_paused_to_active_resume(self, store: RuntimeBindingStore) -> None:
        b = _base(binding_id="rtb-001")
        store.create(b, single_runtime_enforced=False)
        store.transition_status("rtb-001", "pending_pause")
        store.transition_status("rtb-001", "paused")
        updated = store.transition_status("rtb-001", "active")
        assert updated.status == "active"

    def test_active_to_retired(self, store: RuntimeBindingStore) -> None:
        b = _base(binding_id="rtb-001")
        store.create(b, single_runtime_enforced=False)
        updated = store.retire("rtb-001")
        assert updated.status == "retired"
        assert updated.retired_at is not None
        assert updated.is_terminal()

    def test_active_to_failed(self, store: RuntimeBindingStore) -> None:
        b = _base(binding_id="rtb-001")
        store.create(b, single_runtime_enforced=False)
        updated = store.transition_status("rtb-001", "failed")
        assert updated.is_terminal()
        assert updated.retired_at is not None

    def test_terminal_rejects_further_transitions(self, store: RuntimeBindingStore) -> None:
        b = _base(binding_id="rtb-001")
        store.create(b, single_runtime_enforced=False)
        store.retire("rtb-001")
        with pytest.raises(RuntimeBindingError):
            store.transition_status("rtb-001", "active")

    def test_invalid_transition_rejected(self, store: RuntimeBindingStore) -> None:
        b = _base(binding_id="rtb-001")
        store.create(b, single_runtime_enforced=False)
        with pytest.raises(RuntimeBindingError):
            store.transition_status("rtb-001", "paused")  # active -> paused not allowed

    def test_terminal_requires_retired_at_retired(self) -> None:
        b = _base(status="retired", retired_at=None)
        errors = validate_binding(b)
        assert any("retired_at" in e for e in errors)

    def test_terminal_requires_retired_at_failed(self) -> None:
        b = _base(status="failed", retired_at=None)
        errors = validate_binding(b)
        assert any("retired_at" in e for e in errors)


# ---------------------------------------------------------------------------
# Single-runtime enforcement (contract.md §6)
# ---------------------------------------------------------------------------

class TestSingleRuntimeRule:

    def test_single_runtime_blocks_second_active(self, store: RuntimeBindingStore) -> None:
        b1 = _base(binding_id="rtb-001")
        store.create(b1, single_runtime_enforced=True)
        b2 = _base(binding_id="rtb-002")
        with pytest.raises(RuntimeBindingError):
            store.create(b2, single_runtime_enforced=True)

    def test_second_binding_allowed_after_retire(self, store: RuntimeBindingStore) -> None:
        b1 = _base(binding_id="rtb-001")
        store.create(b1, single_runtime_enforced=True)
        store.retire("rtb-001")
        b2 = _base(binding_id="rtb-002")
        created = store.create(b2, single_runtime_enforced=True)
        assert created.binding_id == "rtb-002"

    def test_single_runtime_disabled_allows_concurrent(self, store: RuntimeBindingStore) -> None:
        b1 = _base(binding_id="rtb-001")
        b2 = _base(binding_id="rtb-002")
        store.create(b1, single_runtime_enforced=False)
        store.create(b2, single_runtime_enforced=False)
        assert store.get("rtb-001") is not None
        assert store.get("rtb-002") is not None


class TestAtomicCutover:

    def test_cutover_persists_child_and_retired_source_together(
        self, store: RuntimeBindingStore
    ) -> None:
        source = _base(binding_id="rtb-source")
        replacement = _base(
            binding_id="rtb-child",
            runtime_id="rt-child",
            plan_id="plan-child",
            artifact_id="art-child",
            artifact_version="2.0.0",
        )
        store.create(source)

        retired, child = store.cutover("rtb-source", replacement)

        assert retired.status == RuntimeBindingStatus.RETIRED.value
        assert child.status == RuntimeBindingStatus.ACTIVE.value
        assert store.get_active_for_pool("pool-alpha") == child

    def test_cutover_canonical_write_failure_is_restart_atomic(
        self,
        persisted_store: RuntimeBindingStore,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source = _base(binding_id="rtb-source")
        replacement = _base(
            binding_id="rtb-child",
            runtime_id="rt-child",
            plan_id="plan-child",
            artifact_id="art-child",
            artifact_version="2.0.0",
        )
        persisted_store.create(source)
        store_path = tmp_path / "bindings.json"
        real_write = persisted_store._write_records

        def interrupt_primary(path, records):
            if path == store_path:
                raise RuntimeError("injected primary snapshot failure")
            return real_write(path, records)

        monkeypatch.setattr(persisted_store, "_write_records", interrupt_primary)
        with pytest.raises(RuntimeError, match="primary snapshot failure"):
            persisted_store.cutover("rtb-source", replacement)

        assert persisted_store.require("rtb-source").status == "active"
        assert persisted_store.get("rtb-child") is None
        restarted = RuntimeBindingStore(path=store_path)
        assert restarted.require("rtb-source").status == "active"
        assert restarted.get("rtb-child") is None

    def test_cutover_rejects_an_unrelated_active_owner(
        self, store: RuntimeBindingStore
    ) -> None:
        source = _base(binding_id="rtb-source")
        unrelated = _base(binding_id="rtb-unrelated", runtime_id="rt-unrelated")
        replacement = _base(binding_id="rtb-child", runtime_id="rt-child")
        store.create(source, single_runtime_enforced=False)
        store.create(unrelated, single_runtime_enforced=False)

        with pytest.raises(RuntimeBindingError, match="another active"):
            store.cutover("rtb-source", replacement)

    def test_cutover_cannot_skip_pending_pause_state(self, store: RuntimeBindingStore) -> None:
        source = _base(binding_id="rtb-source")
        replacement = _base(binding_id="rtb-child", runtime_id="rt-child")
        store.create(source)
        store.transition_status("rtb-source", RuntimeBindingStatus.PENDING_PAUSE.value)

        with pytest.raises(RuntimeBindingError, match="status machine"):
            store.cutover("rtb-source", replacement)


class TestPersistenceFailureAtomicity:

    def test_create_save_failure_restores_in_memory_state(
        self, store: RuntimeBindingStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            store,
            "_save",
            lambda: (_ for _ in ()).throw(RuntimeError("injected save failure")),
        )

        with pytest.raises(RuntimeError, match="injected save failure"):
            store.create(_base(binding_id="rtb-create-failure"))

        assert store.get("rtb-create-failure") is None

    def test_transition_save_failure_restores_in_memory_state(
        self, store: RuntimeBindingStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        binding = _base(binding_id="rtb-transition-failure")
        store.create(binding)
        monkeypatch.setattr(
            store,
            "_save",
            lambda: (_ for _ in ()).throw(RuntimeError("injected save failure")),
        )

        with pytest.raises(RuntimeError, match="injected save failure"):
            store.transition_status(
                binding.binding_id,
                RuntimeBindingStatus.PENDING_PAUSE.value,
            )

        assert store.require(binding.binding_id).status == "active"


class TestSingleRuntimeQueries:

    def test_get_active_for_pool_returns_single(self, store: RuntimeBindingStore) -> None:
        b = _base(binding_id="rtb-001")
        store.create(b, single_runtime_enforced=True)
        active = store.get_active_for_pool("pool-alpha")
        assert active is not None
        assert active.binding_id == "rtb-001"

    def test_get_active_for_pool_none_after_retire(self, store: RuntimeBindingStore) -> None:
        b = _base(binding_id="rtb-001")
        store.create(b, single_runtime_enforced=True)
        store.retire("rtb-001")
        assert store.get_active_for_pool("pool-alpha") is None

    def test_different_pools_not_constrained(self, store: RuntimeBindingStore) -> None:
        b1 = _base(binding_id="rtb-001", capital_pool_id="pool-alpha")
        b2 = _base(binding_id="rtb-002", capital_pool_id="pool-beta")
        store.create(b1, single_runtime_enforced=True)
        store.create(b2, single_runtime_enforced=True)
        assert store.get("rtb-001") is not None
        assert store.get("rtb-002") is not None


# ---------------------------------------------------------------------------
# Rollback semantics (contract.md §7)
# ---------------------------------------------------------------------------

class TestRollbackFields:

    def test_valid_rollback_binding(self) -> None:
        b = _base(
            binding_id="rtb-002",
            rollback_parent="rtb-001",
            rollback_action_type=RollbackActionType.REPLACE.value,
        )
        assert validate_binding(b) == []

    def test_rollback_action_type_required_with_parent(self) -> None:
        b = _base(
            binding_id="rtb-002",
            rollback_parent="rtb-001",
            rollback_action_type=None,
        )
        errors = validate_binding(b)
        assert any("rollback_action_type" in e for e in errors)

    def test_invalid_rollback_action_type_raises(self) -> None:
        with pytest.raises(RuntimeBindingError):
            _base(rollback_action_type="liquidate_and_burn")

    def test_all_rollback_action_types_accepted(self) -> None:
        for action in RollbackActionType:
            b = _base(rollback_parent="rtb-parent", rollback_action_type=action.value)
            assert b.rollback_action_type == action.value


# ---------------------------------------------------------------------------
# Store read helpers
# ---------------------------------------------------------------------------

class TestStoreReadHelpers:

    def test_get_returns_none_for_missing(self, store: RuntimeBindingStore) -> None:
        assert store.get("nonexistent") is None

    def test_require_raises_for_missing(self, store: RuntimeBindingStore) -> None:
        with pytest.raises(RuntimeBindingError):
            store.require("nonexistent")

    def test_list_all_returns_all(self, store: RuntimeBindingStore) -> None:
        b1 = _base(binding_id="rtb-001", capital_pool_id="pool-alpha")
        b2 = _base(binding_id="rtb-002", capital_pool_id="pool-beta")
        store.create(b1, single_runtime_enforced=True)
        store.create(b2, single_runtime_enforced=True)
        assert len(store.list_all()) == 2

    def test_find_by_pool(self, store: RuntimeBindingStore) -> None:
        b1 = _base(binding_id="rtb-001", capital_pool_id="pool-alpha")
        b2 = _base(binding_id="rtb-002", capital_pool_id="pool-beta")
        store.create(b1, single_runtime_enforced=True)
        store.create(b2, single_runtime_enforced=True)
        results = store.find_by_pool("pool-alpha")
        assert len(results) == 1
        assert results[0].binding_id == "rtb-001"

    def test_find_by_plan(self, store: RuntimeBindingStore) -> None:
        b1 = _base(binding_id="rtb-001", plan_id="plan-001")
        b2 = _base(binding_id="rtb-002", plan_id="plan-002", capital_pool_id="pool-beta")
        store.create(b1, single_runtime_enforced=True)
        store.create(b2, single_runtime_enforced=True)
        results = store.find_by_plan("plan-001")
        assert len(results) == 1
        assert results[0].binding_id == "rtb-001"

    def test_duplicate_create_rejected(self, store: RuntimeBindingStore) -> None:
        b = _base(binding_id="rtb-001")
        store.create(b, single_runtime_enforced=True)
        with pytest.raises(RuntimeBindingError):
            store.create(b, single_runtime_enforced=False)


# ---------------------------------------------------------------------------
# Persistence round-trip
# ---------------------------------------------------------------------------

class TestPersistence:

    def test_store_and_reload(self, persisted_store: RuntimeBindingStore, tmp_path: Path) -> None:
        b = _base(binding_id="rtb-001")
        persisted_store.create(b, single_runtime_enforced=False)

        store2 = RuntimeBindingStore(path=tmp_path / "bindings.json")
        loaded = store2.get("rtb-001")
        assert loaded is not None
        assert loaded.binding_id == b.binding_id
        assert loaded.plan_id == b.plan_id
        assert loaded.deployment_mode == b.deployment_mode
        assert loaded.persona_capital_binding_id == b.persona_capital_binding_id

    def test_transition_persists(self, persisted_store: RuntimeBindingStore, tmp_path: Path) -> None:
        b = _base(binding_id="rtb-001")
        persisted_store.create(b, single_runtime_enforced=False)
        persisted_store.transition_status("rtb-001", "pending_pause")

        store2 = RuntimeBindingStore(path=tmp_path / "bindings.json")
        loaded = store2.get("rtb-001")
        assert loaded is not None
        assert loaded.status == "pending_pause"

    def test_non_empty_store_writes_backup_snapshot(
        self,
        persisted_store: RuntimeBindingStore,
        tmp_path: Path,
    ) -> None:
        b = _base(binding_id="rtb-001")
        persisted_store.create(b, single_runtime_enforced=False)

        backup_path = tmp_path / "bindings.json.bak"
        assert backup_path.exists()

        store2 = RuntimeBindingStore(path=backup_path)
        loaded = store2.get("rtb-001")
        assert loaded is not None
        assert loaded.binding_id == b.binding_id

    def test_existing_primary_store_backfills_missing_backup(
        self,
        persisted_store: RuntimeBindingStore,
        tmp_path: Path,
    ) -> None:
        b = _base(binding_id="rtb-001")
        persisted_store.create(b, single_runtime_enforced=False)

        backup_path = tmp_path / "bindings.json.bak"
        backup_path.unlink()

        store2 = RuntimeBindingStore(path=tmp_path / "bindings.json")
        loaded = store2.get("rtb-001")
        assert loaded is not None
        assert loaded.binding_id == b.binding_id
        assert backup_path.exists()

    def test_missing_primary_store_restores_from_backup(
        self,
        persisted_store: RuntimeBindingStore,
        tmp_path: Path,
    ) -> None:
        b = _base(binding_id="rtb-001")
        persisted_store.create(b, single_runtime_enforced=False)

        store_path = tmp_path / "bindings.json"
        store_path.unlink()

        store2 = RuntimeBindingStore(path=store_path)
        loaded = store2.get("rtb-001")
        assert loaded is not None
        assert loaded.binding_id == b.binding_id
        assert store_path.exists()

    def test_blank_primary_store_restores_from_backup(
        self,
        persisted_store: RuntimeBindingStore,
        tmp_path: Path,
    ) -> None:
        b = _base(binding_id="rtb-001")
        persisted_store.create(b, single_runtime_enforced=False)

        store_path = tmp_path / "bindings.json"
        store_path.write_text("", encoding="utf-8")

        store2 = RuntimeBindingStore(path=store_path)
        loaded = store2.get("rtb-001")
        assert loaded is not None
        assert loaded.binding_id == b.binding_id

    def test_empty_array_primary_store_restores_from_backup(
        self,
        persisted_store: RuntimeBindingStore,
        tmp_path: Path,
    ) -> None:
        b = _base(binding_id="rtb-001")
        persisted_store.create(b, single_runtime_enforced=False)

        store_path = tmp_path / "bindings.json"
        store_path.write_text("[]\n", encoding="utf-8")

        store2 = RuntimeBindingStore(path=store_path)
        loaded = store2.get("rtb-001")
        assert loaded is not None
        assert loaded.binding_id == b.binding_id


class TestRuntimeBindingMetadataPatch:

    def test_transition_status_with_metadata_patch(self, store: RuntimeBindingStore) -> None:
        b = _base(binding_id="rtb-meta-001", metadata={"existing_key": "val1"})
        store.create(b, single_runtime_enforced=False)

        stale_admission = {
            "session_admission": {
                "reason_code": "market_input_stale",
                "source_snapshot_id": "snap-001",
                "source_event_time": "2026-08-22T00:00:00Z",
                "observed_at": "2026-08-22T10:00:00Z",
                "max_age_seconds": 86400,
                "pause_command_ref": "cmd-stale-001",
                "resume_snapshot_id": None,
                "resumed_at": None,
            }
        }
        updated = store.transition_status(
            "rtb-meta-001",
            "pending_pause",
            metadata_patch=stale_admission,
        )
        assert updated.status == "pending_pause"
        assert updated.metadata["existing_key"] == "val1"
        assert updated.metadata["session_admission"]["reason_code"] == "market_input_stale"
        assert updated.metadata["session_admission"]["source_snapshot_id"] == "snap-001"

        paused = store.transition_status(
            "rtb-meta-001",
            "paused",
        )
        assert paused.status == "paused"
        assert paused.metadata["session_admission"]["reason_code"] == "market_input_stale"

        resume_patch = {
            "session_admission": {
                "resume_snapshot_id": "snap-002",
                "resumed_at": "2026-08-22T11:00:00Z",
            }
        }
        resumed = store.transition_status(
            "rtb-meta-001",
            "active",
            metadata_patch=resume_patch,
        )
        assert resumed.status == "active"
        assert resumed.metadata["session_admission"]["source_snapshot_id"] == "snap-001"
        assert resumed.metadata["session_admission"]["resume_snapshot_id"] == "snap-002"
        assert resumed.metadata["session_admission"]["resumed_at"] == "2026-08-22T11:00:00Z"

    def test_patch_metadata_without_status_change(self, store: RuntimeBindingStore) -> None:
        b = _base(binding_id="rtb-patch-001", metadata={"foo": "bar"})
        store.create(b, single_runtime_enforced=False)

        patched = store.patch_metadata("rtb-patch-001", {"baz": 42})
        assert patched.status == "active"
        assert patched.metadata == {"foo": "bar", "baz": 42}
