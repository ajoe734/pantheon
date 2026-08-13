"""Unit and integration tests for ReplicationAdmissionStore and admission filtering."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from services.research.alpha_replication.admission import ReplicationAdmissionStore
from services.research.alpha_replication.controller_state import (
    ControllerState,
    ControllerStateStore,
)
from services.research.alpha_replication.queue import AlphaReplicationQueue
from services.research.alpha_replication.replication_controller import (
    ReplicationControllerConfig,
    run_controller_tick,
)
from services.research.alpha_replication.test_revalidation_worker import (
    FakeAuthority,
    FakeGateResponse,
    _queue_payload,
    _registry_entry,
)


def test_replication_admission_store_crud_and_validation(tmp_path: Path) -> None:
    store = ReplicationAdmissionStore(tmp_path)
    assert store.list_admissions() == []

    payload = {
        "tenant_id": "tenant-test",
        "strategy_spec_id": "spec-001",
        "strategy_id": "strat-001",
        "spec_version": "1.0.0",
        "checksum": "sha256:abc",
        "approval_decision_id": "app-001",
        "approver": "reviewer-a",
        "mode": "initial",
    }
    created = store.create_admission(payload)
    assert created["status"] == "admitted"
    assert created["admission_id"].startswith("adm-")
    assert created["tenant_id"] == "tenant-test"

    fetched = store.get_admission("tenant-test", "spec-001")
    assert fetched == created

    # Idempotent re-admission with identical params
    dup = store.create_admission(payload)
    assert dup == created

    # Conflict on immutable review parameters fails closed
    conflict_payload = dict(payload)
    conflict_payload["checksum"] = "sha256:different"
    with pytest.raises(ValueError, match="ReplicationAdmission binding conflict"):
        store.create_admission(conflict_payload)


def test_unreviewed_spec_and_seeds_without_admission_are_not_executed(tmp_path: Path) -> None:
    seed_path = tmp_path / "distill_seeds.jsonl"
    seed_path.write_text(
        json.dumps({"source_id": "strat-unreviewed"}) + "\n",
        encoding="utf-8",
    )
    authority = FakeAuthority()
    state = ControllerState(
        controller_id="test-controller",
        controller_name="alpha-replication-controller",
        environment="test",
        tenant_id="tenant-test",
        deployment={"git_sha": "test-sha"},
    )
    state_store = ControllerStateStore(tmp_path / "state.json")
    state_store.save(state)

    admission_store = ReplicationAdmissionStore(tmp_path)
    config = ReplicationControllerConfig(
        database_url="postgresql://test",
        registry_url="http://registry.test",
        interval_seconds=10,
        max_ticks=1,
        state_path=tmp_path / "state.json",
        data_dir=tmp_path,
        seed_store_path=seed_path,
        authority=authority,
        admission_store=admission_store,
    )

    unreviewed_entry = _registry_entry(
        _queue_payload(
            tenant_id="tenant-test",
            strategy_spec_id="spec-unreviewed",
            strategy_id="strat-unreviewed",
        )
    )

    with mock.patch(
        "services.research.alpha_replication.replication_controller._get_approved_specs_for_strategy",
        return_value=[unreviewed_entry],
    ):
        result = run_controller_tick(
            config=config,
            state=state,
            store=state_store,
            writer=None,
        )

    # Desired state records zero admitted specs, despite registry/seed presence
    assert result["desired_state"]["approved_spec_count"] == 0
    assert result["reconcile"]["enqueued_new"] == 0
    assert result["reconcile"]["processed"] == 0
    assert len(authority.tasks) == 0


def test_reviewed_admission_executes_and_creates_terminal_run(tmp_path: Path) -> None:
    seed_path = tmp_path / "distill_seeds.jsonl"
    seed_path.write_text(
        json.dumps({"source_id": "strat-reviewed"}) + "\n",
        encoding="utf-8",
    )
    authority = FakeAuthority()
    state = ControllerState(
        controller_id="test-controller",
        controller_name="alpha-replication-controller",
        environment="test",
        tenant_id="tenant-test",
        deployment={"git_sha": "test-sha"},
    )
    state_store = ControllerStateStore(tmp_path / "state.json")
    state_store.save(state)

    admission_store = ReplicationAdmissionStore(tmp_path)
    payload = _queue_payload(
        tenant_id="tenant-test",
        strategy_spec_id="spec-reviewed-001",
        strategy_id="strat-reviewed",
    )
    admission_store.create_admission(
        {
            "tenant_id": "tenant-test",
            "strategy_spec_id": "spec-reviewed-001",
            "strategy_id": "strat-reviewed",
            "spec_version": payload["spec_version"],
            "checksum": payload["checksum"],
            "approval_decision_id": payload["approval_decision_id"],
            "approver": payload["approver"],
        }
    )

    config = ReplicationControllerConfig(
        database_url="postgresql://test",
        registry_url="http://registry.test",
        interval_seconds=10,
        max_ticks=1,
        state_path=tmp_path / "state.json",
        data_dir=tmp_path,
        seed_store_path=seed_path,
        authority=authority,
        admission_store=admission_store,
    )
    reviewed_entry = _registry_entry(payload)

    with mock.patch(
        "services.research.alpha_replication.replication_controller._get_approved_specs_for_strategy",
        return_value=[reviewed_entry],
    ), mock.patch(
        "services.research.alpha_replication.revalidation_worker.AlphaRevalidationWorker._fetch_strategy_spec_entry",
        return_value=reviewed_entry,
    ), mock.patch(
        "services.research.replication.gate.ReplicationGate.evaluate_candidate",
        return_value=FakeGateResponse(True, "passed admission gate"),
    ):
        result = run_controller_tick(
            config=config,
            state=state,
            store=state_store,
            writer=None,
        )

    assert result["desired_state"]["approved_spec_count"] == 1
    assert result["reconcile"]["enqueued_new"] == 1
    assert result["reconcile"]["processed"] == 1
    assert len(authority.runs) == 1
