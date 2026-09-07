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

import tempfile
from pathlib import Path
from typing import Any, Dict
import pytest

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
    build_canonical_owner_adapter,
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


def test_backfill_rejects_tenant_id_only_foreign_records_without_rewriting_ownership() -> None:
    """A record whose only tenant field is `tenantId` for a foreign tenant must never be
    backfilled under a different tenant's transaction, and its tenant identity must never be
    silently rewritten (regression for the independent-review tenantId/tenant_id probe)."""
    canonical_store: Dict[str, Any] = {}
    overlay_data = {
        "per-foreign": {"persona_id": "per-foreign", "tenantId": "tenant-b"},
    }
    engine = OverlayMigrationEngine(
        aggregate=AggregateKind.PERSONA,
        canonical_store=canonical_store,
        overlay_data_source=overlay_data,
    )

    result = engine.backfill(tenant_id="tenant-a")
    assert result.backfilled == 0
    assert "per-foreign" not in canonical_store


def test_backfill_reports_conflicting_tenant_identity_as_conflict() -> None:
    canonical_store: Dict[str, Any] = {}
    overlay_data = {
        "per-conflict": {"persona_id": "per-conflict", "tenant_id": "tenant-a", "tenantId": "tenant-b"},
    }
    engine = OverlayMigrationEngine(
        aggregate=AggregateKind.PERSONA,
        canonical_store=canonical_store,
        overlay_data_source=overlay_data,
    )

    result = engine.backfill(tenant_id="tenant-a")
    assert result.backfilled == 0
    assert "per-conflict" not in canonical_store
    assert any(c.conflict_type == "tenant_identity_conflict" for c in result.conflicts)


def test_backfill_fails_closed_on_unsupported_canonical_store() -> None:
    """A store that supports neither insert/save nor dict semantics must never report a
    fabricated backfilled=1; it must fail closed and surface a conflict instead."""
    unsupported_store = object()
    overlay_data = {"per-x": {"persona_id": "per-x", "tenant_id": "tenant-a"}}
    engine = OverlayMigrationEngine(
        aggregate=AggregateKind.PERSONA,
        canonical_store=unsupported_store,
        overlay_data_source=overlay_data,
    )

    result = engine.backfill(tenant_id="tenant-a")
    assert result.backfilled == 0
    assert any(c.conflict_type == "unsupported_canonical_store" for c in result.conflicts)


def test_diff_records_detects_missing_canonical_field() -> None:
    engine = OverlayMigrationEngine(
        aggregate=AggregateKind.PERSONA,
        canonical_store={},
        overlay_data_source={},
    )
    diffs = engine._diff_records({"persona_id": "p1"}, {"persona_id": "p1", "name": "Algo"})
    assert "name" in diffs
    assert diffs["name"] == {"canonical": None, "overlay": "Algo"}


def test_shadow_compare_reports_divergence_for_field_missing_only_in_canonical() -> None:
    canonical_store = {"per-1": {"persona_id": "per-1", "tenant_id": "tenant-a"}}
    overlay_data = {"per-1": {"persona_id": "per-1", "tenant_id": "tenant-a", "name": "Algo 1"}}
    engine = OverlayMigrationEngine(
        aggregate=AggregateKind.PERSONA,
        canonical_store=canonical_store,
        overlay_data_source=overlay_data,
    )

    report = engine.shadow_compare(tenant_id="tenant-a")
    assert report.matched_count == 0
    assert report.divergent_count == 1
    assert report.parity_ratio == 0.0


# ---------------------------------------------------------------------------
# 4. Single Canonical Writer & Forbidden Fallback Acknowledgement
# ---------------------------------------------------------------------------

def test_canonical_writer_enforcement_and_rejection_of_fallbacks() -> None:
    persona_store: Dict[str, Any] = {}
    coordinator = CanonicalWriterCoordinator(canonical_stores={AggregateKind.PERSONA: persona_store})

    # Canonical writer succeeds and actually persists the record.
    receipt = coordinator.handle_write(
        aggregate=AggregateKind.PERSONA,
        writer_identity="persona_provisioning_store",
        payload={"persona_id": "p1", "name": "Algo 1"},
        is_fallback=False,
    )
    assert receipt["status"] == "acknowledged"
    assert receipt["writer"] == "persona_provisioning_store"
    assert receipt["persisted"] is True
    assert persona_store["p1"]["name"] == "Algo 1"

    # No canonical store bound for this aggregate: refuse to fabricate a receipt.
    with pytest.raises(FallbackAcknowledgementForbiddenError, match="No canonical store bound"):
        CanonicalWriterCoordinator().handle_write(
            aggregate=AggregateKind.STRATEGY,
            writer_identity="strategy_spec_store",
            payload={"strategy_id": "s1"},
            is_fallback=False,
        )

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


# ---------------------------------------------------------------------------
# 8. Genuine Five-Owner Durable Backfill, Shadow Conflicts & Multi-Replica Durability
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "aggregate,key_field,canonical_owner",
    [
        (AggregateKind.PERSONA, "persona_id", "persona_provisioning_store"),
        (AggregateKind.STRATEGY, "strategy_id", "strategy_spec_store"),
        (AggregateKind.INCIDENT, "incident_id", "incident_reconciliation_store"),
        (AggregateKind.JOB, "job_id", "job_service_store"),
        (AggregateKind.RANKING, "snapshot_id", "ranking_domain_store"),
    ],
)
def test_genuine_five_owner_backfill_shadow_conflicts_and_idempotency(
    aggregate: AggregateKind, key_field: str, canonical_owner: str
) -> None:
    """Normative SD §5.1, §5.2: verify genuine durable owner backfill, shadow conflict reporting,
    dry-run protection, and parity across each of the five domain aggregates.
    """
    with tempfile.TemporaryDirectory() as td:
        durable_store = build_canonical_owner_adapter(aggregate=aggregate, storage_dir=td)

        # 1. Seed durable store with initial existing canonical record
        initial_canon = {
            key_field: f"{aggregate.value}-canon-1",
            "name": f"Canonical {aggregate.value.title()} 1",
            "tenant_id": "tenant-corp",
            "status": "active",
        }
        assert durable_store.insert(initial_canon) is True

        # 2. Prepare overlay data source with:
        #   - 1 matching record
        #   - 1 divergent record (field divergence conflict)
        #   - 1 missing record (to be backfilled)
        overlay_data = {
            f"{aggregate.value}-canon-1": {
                key_field: f"{aggregate.value}-canon-1",
                "name": f"Canonical {aggregate.value.title()} 1",
                "tenant_id": "tenant-corp",
                "status": "active",
            },
            f"{aggregate.value}-divergent": {
                key_field: f"{aggregate.value}-divergent",
                "name": "Overlay Version Divergent",
                "tenant_id": "tenant-corp",
                "status": "draft",
            },
            f"{aggregate.value}-to-backfill": {
                key_field: f"{aggregate.value}-to-backfill",
                "name": f"Backfilled {aggregate.value.title()}",
                "tenant_id": "tenant-corp",
                "status": "active",
            },
        }

        # Seed the divergent record into canonical store with different status
        assert durable_store.insert({
            key_field: f"{aggregate.value}-divergent",
            "name": "Canonical Version Divergent",
            "tenant_id": "tenant-corp",
            "status": "active",
        }) is True

        engine = OverlayMigrationEngine(
            aggregate=aggregate,
            canonical_store=durable_store,
            overlay_data_source=overlay_data,
        )

        # 3. Shadow-compare: must detect 1 match, 1 divergence, 1 missing
        report = engine.shadow_compare(tenant_id="tenant-corp")
        assert report.aggregate == aggregate
        assert report.scanned_canonical == 2
        assert report.scanned_overlay == 3
        assert report.matched_count == 1
        assert report.divergent_count == 1
        assert report.missing_in_canonical_count == 1
        assert len(report.conflicts) == 2
        conflict_types = {c.conflict_type for c in report.conflicts}
        assert "field_divergence" in conflict_types
        assert "missing_in_canonical" in conflict_types

        # 4. Dry-run backfill: must report 1 backfillable record without mutating persistent storage
        dry_result = engine.backfill(tenant_id="tenant-corp", dry_run=True)
        assert dry_result.dry_run is True
        assert dry_result.backfilled == 1
        assert dry_result.skipped_existing == 2
        # Verify durable store on disk still only contains the original 2 records
        assert len(durable_store.list_records(tenant_id="tenant-corp")) == 2

        # 5. Live backfill: persists missing record to disk with provenance
        live_result = engine.backfill(tenant_id="tenant-corp", dry_run=False)
        assert live_result.dry_run is False
        assert live_result.backfilled == 1
        assert live_result.skipped_existing == 2
        # Verify durable store on disk now contains 3 records
        persisted_records = durable_store.list_records(tenant_id="tenant-corp")
        assert len(persisted_records) == 3
        backfilled_rec = durable_store.get(f"{aggregate.value}-to-backfill")
        assert backfilled_rec is not None
        assert backfilled_rec["_migration_metadata"]["source"] == "overlay_retire_001"
        assert backfilled_rec["_migration_metadata"]["checksum"] is not None

        # 6. Re-run backfill: must be strictly idempotent (0 backfilled, 3 skipped)
        idempotent_result = engine.backfill(tenant_id="tenant-corp", dry_run=False)
        assert idempotent_result.backfilled == 0
        assert idempotent_result.skipped_existing == 3

        # 7. CanonicalWriterCoordinator sole owner verification for this aggregate
        coordinator = CanonicalWriterCoordinator(canonical_stores={aggregate: durable_store})
        receipt = coordinator.handle_write(
            aggregate=aggregate,
            writer_identity=canonical_owner,
            payload={
                key_field: f"{aggregate.value}-coord-write",
                "name": f"Coordinator Written {aggregate.value.title()}",
                "tenant_id": "tenant-corp",
            },
            is_fallback=False,
        )
        assert receipt["status"] == "acknowledged"
        assert receipt["persisted"] is True
        assert receipt["writer"] == canonical_owner
        assert durable_store.get(f"{aggregate.value}-coord-write") is not None


def test_genuine_five_owner_disk_backed_multi_replica_restart_durability() -> None:
    """Normative SD §5.1, §5.2, §12.3: Prove multi-replica readback and process restart
    across independent process replicas for all five domain owners using filesystem backing.
    """
    with tempfile.TemporaryDirectory() as td:
        harness = MultiReplicaReadbackHarness(shared_durable_storage=td)

        replica_alpha = harness.spawn_replica("replica-alpha")
        replica_beta = harness.spawn_replica("replica-beta")

        # Test all 5 aggregates
        aggregates = [
            (AggregateKind.PERSONA, "pers-durable-1", {"name": "Persona 1", "state": "active"}),
            (AggregateKind.STRATEGY, "strat-durable-1", {"title": "Strategy 1", "lifecycle_state": "active"}),
            (AggregateKind.INCIDENT, "inc-durable-1", {"title": "Incident 1", "status": "open"}),
            (AggregateKind.JOB, "job-durable-1", {"name": "Job 1", "status": "running"}),
            (AggregateKind.RANKING, "rank-durable-1", {"formula": "sharpe", "score": 2.5}),
        ]

        # Replica Alpha writes all 5 aggregate canonical records directly to durable disk
        for agg, key, payload in aggregates:
            record = {"id": key, "aggregate": agg.value, **payload}
            replica_alpha.write_canonical(key, record)

        # Simulate hard process restart on Replica Alpha (memory wiped, local handles dropped)
        replica_alpha.restart_process()

        # Replica Alpha reads back after restart: must survive on disk
        for agg, key, payload in aggregates:
            readback_alpha = replica_alpha.read_canonical(key)
            assert readback_alpha is not None
            assert readback_alpha["id"] == key
            assert readback_alpha["aggregate"] == agg.value

            # Verify genuine independent subprocess readback
            readback_proc = replica_alpha.read_canonical_via_restarted_process(key)
            assert readback_proc is not None
            assert readback_proc["id"] == key
            assert readback_proc["aggregate"] == agg.value
            assert readback_proc == readback_alpha

        # Replica Beta (completely independent replica) reads directly from durable storage
        for agg, key, payload in aggregates:
            readback_beta = replica_beta.read_canonical(key)
            assert readback_beta is not None
            assert readback_beta == replica_alpha.read_canonical(key)
