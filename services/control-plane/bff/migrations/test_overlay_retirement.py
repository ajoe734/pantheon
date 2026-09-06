"""Comprehensive test suite for OVERLAY-RETIRE-001 migration engine and acceptance criteria.

Covers:
  1. Shadow-comparison and conflict reports across Persona, Strategy, Incident, Job, Ranking.
  2. Parity detection, field divergence, and checksum provenance.
  3. Resumable cursor pagination and dry-run safety for backfill.
  4. Tenant transaction boundary isolation.
  5. Single canonical writer enforcement and FallbackAcknowledgementForbiddenError.
  6. Restart durability and multi-replica readback pass with 0 overlay reliance.
  7. Rollback policy assertions: never re-enable dual writes.
  8. Verification of mandatory symbol retirements.
"""
from __future__ import annotations

import pytest
from typing import Any, Dict

from services.control_plane.bff.migrations.overlay_retirement import (
    AggregateKind,
    BackfillResult,
    CanonicalWriterCoordinator,
    ConflictReport,
    DualWriteForbiddenError,
    FallbackAcknowledgementForbiddenError,
    MultiReplicaReadbackHarness,
    OverlayMigrationEngine,
    RollbackPolicy,
    assert_mandatory_symbol_retirements,
    deterministic_checksum,
)


# ---------------------------------------------------------------------------
# 1. Shadow-Compare & Conflict Reporting (Persona, Strategy, Incident, Job, Ranking)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "aggregate,key_field",
    [
        (AggregateKind.PERSONA, "persona_id"),
        (AggregateKind.STRATEGY, "strategy_id"),
        (AggregateKind.INCIDENT, "incident_id"),
        (AggregateKind.JOB, "job_id"),
        (AggregateKind.RANKING, "snapshot_id"),
    ],
)
def test_shadow_compare_all_aggregates_clean_parity(aggregate: AggregateKind, key_field: str) -> None:
    canonical_store = {
        "item-1": {key_field: "item-1", "name": "Item One", "status": "active", "tenant_id": "tenant-a"},
        "item-2": {key_field: "item-2", "name": "Item Two", "status": "active", "tenant_id": "tenant-a"},
    }
    overlay_data = {
        "item-1": {key_field: "item-1", "name": "Item One", "status": "active", "tenant_id": "tenant-a"},
        "item-2": {key_field: "item-2", "name": "Item Two", "status": "active", "tenant_id": "tenant-a"},
    }
    engine = OverlayMigrationEngine(
        aggregate=aggregate,
        canonical_store=canonical_store,
        overlay_data_source=overlay_data,
    )
    report = engine.shadow_compare(tenant_id="tenant-a")

    assert isinstance(report, ConflictReport)
    assert report.aggregate == aggregate
    assert report.scanned_canonical == 2
    assert report.scanned_overlay == 2
    assert report.matched_count == 2
    assert report.missing_in_canonical_count == 0
    assert report.divergent_count == 0
    assert report.parity_ratio == 1.0
    assert len(report.conflicts) == 0


def test_shadow_compare_divergence_and_missing_records() -> None:
    canonical_store = {
        "strat-1": {"strategy_id": "strat-1", "title": "Strat 1", "lifecycle_state": "active"},
        "strat-2": {"strategy_id": "strat-2", "title": "Strat 2 Canonical", "lifecycle_state": "active"},
    }
    overlay_data = {
        "strat-1": {"strategy_id": "strat-1", "title": "Strat 1", "lifecycle_state": "active"},
        "strat-2": {"strategy_id": "strat-2", "title": "Strat 2 Overlay Modified", "lifecycle_state": "active"},
        "strat-3": {"strategy_id": "strat-3", "title": "Strat 3 Only in Overlay", "lifecycle_state": "draft"},
    }
    engine = OverlayMigrationEngine(
        aggregate=AggregateKind.STRATEGY,
        canonical_store=canonical_store,
        overlay_data_source=overlay_data,
    )
    report = engine.shadow_compare()

    assert report.matched_count == 1
    assert report.divergent_count == 1
    assert report.missing_in_canonical_count == 1
    assert report.parity_ratio == pytest.approx(1 / 3)
    assert len(report.conflicts) == 2

    conflict_types = {c.conflict_type for c in report.conflicts}
    assert "missing_in_canonical" in conflict_types
    assert "field_divergence" in conflict_types

    diff_conflict = next(c for c in report.conflicts if c.conflict_type == "field_divergence")
    assert diff_conflict.record_id == "strat-2"
    assert "title" in diff_conflict.divergent_fields


# ---------------------------------------------------------------------------
# 2. Backfill with Dry Run, Resumable Cursor, and Checksum Provenance
# ---------------------------------------------------------------------------

def test_backfill_dry_run_does_not_mutate_canonical_store() -> None:
    canonical_store = {
        "per-1": {"persona_id": "per-1", "name": "Persona 1", "tenant_id": "tenant-corp"},
    }
    overlay_data = {
        "per-1": {"persona_id": "per-1", "name": "Persona 1", "tenant_id": "tenant-corp"},
        "per-2": {"persona_id": "per-2", "name": "Persona 2", "tenant_id": "tenant-corp"},
    }
    engine = OverlayMigrationEngine(
        aggregate=AggregateKind.PERSONA,
        canonical_store=canonical_store,
        overlay_data_source=overlay_data,
    )
    result = engine.backfill(tenant_id="tenant-corp", dry_run=True)

    assert result.dry_run is True
    assert result.backfilled == 1
    assert result.skipped_existing == 1
    assert "per-2" not in canonical_store  # not mutated due to dry_run


def test_backfill_mutates_with_checksum_and_provenance() -> None:
    canonical_store = {
        "inc-1": {"incident_id": "inc-1", "status": "open", "tenant_id": "tenant-corp"},
    }
    overlay_data = {
        "inc-1": {"incident_id": "inc-1", "status": "open", "tenant_id": "tenant-corp"},
        "inc-2": {"incident_id": "inc-2", "status": "investigating", "severity": "high", "tenant_id": "tenant-corp"},
    }
    engine = OverlayMigrationEngine(
        aggregate=AggregateKind.INCIDENT,
        canonical_store=canonical_store,
        overlay_data_source=overlay_data,
    )
    result = engine.backfill(tenant_id="tenant-corp", dry_run=False)

    assert result.dry_run is False
    assert result.backfilled == 1
    assert result.skipped_existing == 1
    assert "inc-2" in canonical_store

    backfilled_record = canonical_store["inc-2"]
    assert backfilled_record["_migration_metadata"]["source"] == "overlay_retire_001"
    assert "checksum" in backfilled_record["_migration_metadata"]
    assert "backfilled_at" in backfilled_record["_migration_metadata"]


def test_backfill_resumable_cursor_pagination() -> None:
    canonical_store = {}
    overlay_data = {
        f"job-{i}": {"job_id": f"job-{i}", "status": "completed", "tenant_id": "tenant-x"}
        for i in range(10)
    }
    engine = OverlayMigrationEngine(
        aggregate=AggregateKind.JOB,
        canonical_store=canonical_store,
        overlay_data_source=overlay_data,
    )

    # Page 1: 4 items
    res1 = engine.backfill(tenant_id="tenant-x", cursor=0, page_size=4)
    assert res1.backfilled == 4
    assert res1.next_cursor == "4"
    assert len(canonical_store) == 4

    # Page 2: 4 items
    res2 = engine.backfill(tenant_id="tenant-x", cursor=int(res1.next_cursor), page_size=4)
    assert res2.backfilled == 4
    assert res2.next_cursor == "8"
    assert len(canonical_store) == 8

    # Page 3: remaining 2 items
    res3 = engine.backfill(tenant_id="tenant-x", cursor=int(res2.next_cursor), page_size=4)
    assert res3.backfilled == 2
    assert res3.next_cursor is None
    assert len(canonical_store) == 10


# ---------------------------------------------------------------------------
# 3. Tenant Boundary Isolation
# ---------------------------------------------------------------------------

def test_backfill_respects_tenant_boundary() -> None:
    canonical_store = {}
    overlay_data = {
        "per-a1": {"persona_id": "per-a1", "tenant_id": "tenant-alpha"},
        "per-b1": {"persona_id": "per-b1", "tenant_id": "tenant-beta"},
    }
    engine = OverlayMigrationEngine(
        aggregate=AggregateKind.PERSONA,
        canonical_store=canonical_store,
        overlay_data_source=overlay_data,
    )

    engine.backfill(tenant_id="tenant-alpha")
    assert "per-a1" in canonical_store
    assert "per-b1" not in canonical_store


# ---------------------------------------------------------------------------
# 4. Single Canonical Writer & Forbidden Fallback Acknowledgement
# ---------------------------------------------------------------------------

def test_canonical_writer_enforcement_and_rejection_of_fallbacks() -> None:
    coordinator = CanonicalWriterCoordinator()

    # Canonical writer succeeds
    receipt = coordinator.handle_write(
        aggregate=AggregateKind.PERSONA,
        writer_identity="persona_provisioning_store",
        payload={"persona_id": "p1", "name": "Algo 1"},
        is_fallback=False,
    )
    assert receipt["status"] == "acknowledged"
    assert receipt["writer"] == "persona_provisioning_store"

    # Unauthorized writer fails
    with pytest.raises(FallbackAcknowledgementForbiddenError, match="Unauthorized writer"):
        coordinator.handle_write(
            aggregate=AggregateKind.PERSONA,
            writer_identity="random_unauthorized_service",
            payload={"persona_id": "p1"},
            is_fallback=False,
        )

    # Fallback acknowledgement write strictly forbidden
    with pytest.raises(FallbackAcknowledgementForbiddenError, match="Fallback write attempt forbidden"):
        coordinator.handle_write(
            aggregate=AggregateKind.PERSONA,
            writer_identity="persona_provisioning_store",
            payload={"persona_id": "p1"},
            is_fallback=True,
        )


# ---------------------------------------------------------------------------
# 5. Restart Durability & Multi-Replica Readback Verification
# ---------------------------------------------------------------------------

def test_restart_durability_and_multi_replica_readback() -> None:
    shared_storage: Dict[str, Any] = {}
    harness = MultiReplicaReadbackHarness(shared_storage)

    replica_1 = harness.spawn_replica("replica-east-1")
    replica_2 = harness.spawn_replica("replica-east-2")

    # Replica 1 performs write to canonical store
    record = {
        "persona_id": "pers-canonical-999",
        "name": "Market Maker Canary",
        "state": "paper_running",
    }
    replica_1.write_canonical("pers-canonical-999", record)

    # Simulate crash / restart of replica 1
    replica_1.restart_process()

    # Replica 1 reads back after restart: must survive
    readback_rep1 = replica_1.read_canonical("pers-canonical-999")
    assert readback_rep1 is not None
    assert readback_rep1["persona_id"] == "pers-canonical-999"
    assert readback_rep1["name"] == "Market Maker Canary"

    # Replica 2 immediately observes the exact same state without local overlay
    readback_rep2 = replica_2.read_canonical("pers-canonical-999")
    assert readback_rep2 is not None
    assert readback_rep2 == readback_rep1


# ---------------------------------------------------------------------------
# 6. Governed Rollback Policy
# ---------------------------------------------------------------------------

def test_rollback_policy_strictly_forbids_dual_writes() -> None:
    policy = RollbackPolicy.get_policy_declaration()
    assert policy["rule"] == "Deploy the exact prior compatible release; never re-enable dual writes."
    assert policy["dual_writes_permitted"] is False
    assert policy["fallback_acknowledgement_permitted"] is False

    # Safe rollback assertion passes
    RollbackPolicy.assert_safe_rollback(allow_dual_writes=False)

    # Attempt to enable dual writes during rollback is forbidden
    with pytest.raises(DualWriteForbiddenError, match="Never re-enable dual writes"):
        RollbackPolicy.assert_safe_rollback(allow_dual_writes=True)


# ---------------------------------------------------------------------------
# 7. Mandatory Symbol Retirements
# ---------------------------------------------------------------------------

def test_mandatory_symbol_retirements_in_codebase() -> None:
    results = assert_mandatory_symbol_retirements()
    assert results["_PERSONA_BFF_OVERLAY"] is True
    assert results["_STRATEGY_BFF_OVERLAY"] is True
    assert results["_GOV_BFF_INCIDENT_OVERLAY"] is True
    assert results["_GOV_BFF_JOB_OVERLAY"] is True
    assert results["ReadSurfacePorts._ranking_snapshots"] is True
