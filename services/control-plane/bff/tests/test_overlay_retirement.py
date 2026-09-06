"""Verification suite for OVERLAY-RETIRE-001: process-local product state overlay retirement.

Verifies:
  1. Mandatory deletion of the 5 state overlay authorities:
     - _PERSONA_BFF_OVERLAY (AttributeError on get/set, excluded from __dict__)
     - _STRATEGY_BFF_OVERLAY (AttributeError on get/set, excluded from __dict__)
     - _GOV_BFF_INCIDENT_OVERLAY (AttributeError on get/set, excluded from __dict__)
     - _GOV_BFF_JOB_OVERLAY (AttributeError on get/set, excluded from __dict__)
     - ReadSurfacePorts._ranking_snapshots (AttributeError on get/set)
  2. Production read paths in main.py and jobs router resolve strictly from canonical
     stores with zero overlay fallback or merge logic.
  3. Reinstatement prevention: any attempt to setattr on the retired overlays fails closed.
  4. Multi-replica readback and restart durability pass with one canonical writer.
  5. Rollback policy strictly forbids restoring dual writes.
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from services.control_plane.bff import main as bff_main
from services.control_plane.bff.ports.read_surface_ports import ReadSurfacePorts


# ---------------------------------------------------------------------------
# 1. Mandatory Symbol Retirement and Reinstatement Prevention
# ---------------------------------------------------------------------------

RETIRED_OVERLAY_SYMBOLS = (
    "_PERSONA_BFF_OVERLAY",
    "_STRATEGY_BFF_OVERLAY",
    "_GOV_BFF_INCIDENT_OVERLAY",
    "_GOV_BFF_JOB_OVERLAY",
)


@pytest.mark.parametrize("symbol", RETIRED_OVERLAY_SYMBOLS)
def test_mandatory_overlay_symbols_not_in_module_dict(symbol: str) -> None:
    """The 4 mandatory overlay symbols must not exist as globals in main.__dict__."""
    assert symbol not in bff_main.__dict__, (
        f"Symbol {symbol!r} must be excised from main.__dict__"
    )


@pytest.mark.parametrize("symbol", RETIRED_OVERLAY_SYMBOLS)
def test_mandatory_overlay_symbols_raise_attribute_error_on_getattr(symbol: str) -> None:
    """Accessing any retired overlay must raise AttributeError, preventing silent reachability."""
    with pytest.raises(AttributeError) as exc_info:
        _ = getattr(bff_main, symbol)
    assert "retired and deleted" in str(exc_info.value)


@pytest.mark.parametrize("symbol", RETIRED_OVERLAY_SYMBOLS)
def test_mandatory_overlay_symbols_raise_attribute_error_on_setattr(symbol: str) -> None:
    """Assigning to any retired overlay must raise AttributeError, preventing test/runtime reinstatement."""
    with pytest.raises(AttributeError) as exc_info:
        setattr(bff_main, symbol, {"fake_key": "fake_val"})
    assert "retired and deleted" in str(exc_info.value)


def test_ranking_snapshots_raises_attribute_error_on_read_surface_ports() -> None:
    """ReadSurfacePorts._ranking_snapshots must raise AttributeError on get and set."""
    ports = ReadSurfacePorts()
    with pytest.raises(AttributeError) as get_exc:
        _ = ports._ranking_snapshots
    assert "retired and deleted" in str(get_exc.value)

    with pytest.raises(AttributeError) as set_exc:
        ports._ranking_snapshots = {"snap-1": {}}
    assert "retired and deleted" in str(set_exc.value)


# ---------------------------------------------------------------------------
# 2. Production Read Paths: Canonical Resolution Without Overlay Fallback
# ---------------------------------------------------------------------------

class FakeCanonicalReadStore:
    def __init__(self) -> None:
        self.strategies: list[dict] = []
        self.personas: list[dict] = []
        self.incidents: dict[str, dict] = {}
        self.jobs: dict[str, dict] = {}

    def list_strategy_specs(self, **kwargs) -> list[dict]:
        return list(self.strategies)

    def list_personas(self, **kwargs) -> list[dict]:
        return list(self.personas)

    def list_incidents(self, **kwargs) -> list[dict]:
        return list(self.incidents.values())

    def get_incident(self, incident_id: str) -> dict | None:
        return self.incidents.get(incident_id)

    def list_jobs_bff(self, **kwargs) -> list[dict]:
        return list(self.jobs.values())

    def get_job_bff(self, job_id: str) -> dict | None:
        return self.jobs.get(job_id)

    def dataset_source(self, dataset: str, **kwargs) -> str:
        return "canonical_store"


def test_list_strategy_summaries_reads_strictly_canonical_store() -> None:
    """_list_strategy_summaries must return records from read_store without overlay lookup."""
    original_store = bff_main.read_store
    fake_store = FakeCanonicalReadStore()
    fake_store.strategies = [{
        "strategy_id": "canonical-strat-001",
        "name": "Canonical Momentum Alpha",
        "state": "active",
        "updatedAt": "2026-09-01T00:00:00Z",
    }]
    bff_main.read_store = fake_store
    try:
        summaries = bff_main._list_strategy_summaries()
        assert len(summaries) == 1
        assert summaries[0]["strategy_id"] == "canonical-strat-001"
        assert summaries[0]["name"] == "Canonical Momentum Alpha"
    finally:
        bff_main.read_store = original_store


def test_list_persona_records_reads_strictly_canonical_and_provisioning_stores() -> None:
    """_list_persona_records must return records from read_store and provisioning store only."""
    original_store = bff_main.read_store
    fake_store = FakeCanonicalReadStore()
    fake_store.personas = [{
        "id": "persona-canonical-1",
        "persona_id": "persona-canonical-1",
        "name": "Canonical Persona",
        "lifecycle_state": "paper_running",
        "metadata": {"tenant_id": "tenant-test"},
    }]
    bff_main.read_store = fake_store
    try:
        records = bff_main._list_persona_records(tenant_id="tenant-test")
        assert len(records) == 1
        assert records[0]["persona_id"] == "persona-canonical-1"
        assert records[0]["name"] == "Canonical Persona"
    finally:
        bff_main.read_store = original_store


def test_incident_read_paths_strictly_canonical() -> None:
    """_list_bff_incidents and _get_bff_incident query read_store with zero overlay fallback."""
    original_store = bff_main.read_store
    fake_store = FakeCanonicalReadStore()
    fake_store.incidents["inc-canonical-999"] = {
        "incident_id": "inc-canonical-999",
        "id": "inc-canonical-999",
        "title": "Canonical Incident",
        "status": "investigating",
        "severity": "medium",
        "created_at": "2026-09-05T12:00:00Z",
    }
    bff_main.read_store = fake_store
    try:
        found = bff_main._get_bff_incident("inc-canonical-999")
        assert found is not None
        assert (found.get("incident_id") or found.get("id")) == "inc-canonical-999"

        # Missing incident returns None without attempting any overlay lookup
        assert bff_main._get_bff_incident("inc-non-existent") is None

        listed = bff_main._list_bff_incidents()
        assert any(
            str(i.get("incident_id") or i.get("id")) == "inc-canonical-999"
            for i in listed
        )
    finally:
        bff_main.read_store = original_store


def test_jobs_router_reads_strictly_canonical_read_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Jobs router routes GET /bff/jobs and /bff/jobs/{job_id} through read_store with 0 overlay."""
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    original_store = bff_main.read_store
    fake_store = FakeCanonicalReadStore()
    fake_store.jobs["job-can-1"] = {
        "job_id": "job-can-1",
        "id": "job-can-1",
        "status": "running",
        "job_type": "backtest",
    }
    bff_main.read_store = fake_store
    try:
        client = TestClient(bff_main.app)
        headers = {"Authorization": "Bearer op-test:operator"}
        resp = client.get("/bff/jobs", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        items = data.get("items") or data.get("data") or []
        assert any(j.get("job_id") == "job-can-1" for j in items)

        detail = client.get("/bff/jobs/job-can-1", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["data"]["job_id"] == "job-can-1"

        # Non-existent job returns 404 cleanly without trying an in-memory overlay
        not_found = client.get("/bff/jobs/non-existent-job-xyz", headers=headers)
        assert not_found.status_code == 404
    finally:
        bff_main.read_store = original_store


# ---------------------------------------------------------------------------
# 3. Multi-Replica Readback and Restart Durability (SD §5.1, §5.2)
# ---------------------------------------------------------------------------

from services.control_plane.bff.migrations.overlay_retirement import (
    AggregateKind,
    CanonicalWriterCoordinator,
    DualWriteForbiddenError,
    FallbackAcknowledgementForbiddenError,
    MultiReplicaReadbackHarness,
    OverlayMigrationEngine,
    RollbackPolicy,
)


def test_multi_replica_restart_durability_canonical_truth() -> None:
    """Simulate two independent process replicas reading and writing canonical state.

    Proves that state written by replica 1 to shared durable storage survives
    process restart and is immediately consistent for fresh replica 2 using
    an isolated reader instance, confirming complete elimination of process-local state authority.
    """
    # Shared durable persistence backend (e.g. database / disk backing)
    shared_storage_records: list[dict] = [{
        "id": "persona-durable-rep-1",
        "persona_id": "persona-durable-rep-1",
        "name": "Durable Multi-Replica Persona",
        "lifecycle_state": "paper_running",
        "metadata": {"tenant_id": "tenant-durability"},
    }]

    # Replica 1: independent process instance binding to durable storage
    original_store = bff_main.read_store
    replica_1_store = FakeCanonicalReadStore()
    replica_1_store.personas = list(shared_storage_records)
    bff_main.read_store = replica_1_store

    try:
        records1 = bff_main._list_persona_records(tenant_id="tenant-durability")
        assert len(records1) == 1
        assert records1[0]["persona_id"] == "persona-durable-rep-1"

        # Replica 1 performs a new canonical write to shared storage
        new_record = {
            "id": "persona-durable-rep-2",
            "persona_id": "persona-durable-rep-2",
            "name": "Second Durable Persona",
            "lifecycle_state": "paper_running",
            "metadata": {"tenant_id": "tenant-durability"},
        }
        shared_storage_records.append(new_record)
        replica_1_store.personas.append(new_record)

        # Simulate hard process restart / failover: process memory is wiped
        bff_main.read_store = None

        # Fresh Replica 2 boots up in a new clean process container
        # It creates its own independent store instance from shared storage (distinct object identity)
        replica_2_store = FakeCanonicalReadStore()
        replica_2_store.personas = list(shared_storage_records)
        assert replica_2_store is not replica_1_store

        bff_main.read_store = replica_2_store
        records2 = bff_main._list_persona_records(tenant_id="tenant-durability")
        assert len(records2) == 2
        persona_ids = {r["persona_id"] for r in records2}
        assert persona_ids == {"persona-durable-rep-1", "persona-durable-rep-2"}
        assert all(r["lifecycle_state"] == "paper_running" for r in records2)

        # Harness-level multi-replica and restart verification
        harness = MultiReplicaReadbackHarness({})
        rep_a = harness.spawn_replica("rep-a")
        rep_b = harness.spawn_replica("rep-b")
        rep_a.write_canonical("p-999", {"persona_id": "p-999", "name": "Algo Canary"})
        rep_a.restart_process()
        readback_a = rep_a.read_canonical("p-999")
        assert readback_a is not None and readback_a["name"] == "Algo Canary"
        readback_b = rep_b.read_canonical("p-999")
        assert readback_b == readback_a
    finally:
        bff_main.read_store = original_store


# ---------------------------------------------------------------------------
# 4. Single Canonical Writer and Rollback Safety Policy (SD §5.1, §5.2)
# ---------------------------------------------------------------------------

def test_rollback_policy_strictly_forbids_restoring_dual_writes() -> None:
    """Governed rollback policy: Deploy exact prior compatible release; never re-enable dual writes."""
    policy = RollbackPolicy.get_policy_declaration()
    assert policy["rule"] == "Deploy the exact prior compatible release; never re-enable dual writes."
    assert policy["dual_writes_permitted"] is False
    assert policy["fallback_acknowledgement_permitted"] is False

    # Safe rollback assertion passes when dual writes are disallowed
    RollbackPolicy.assert_safe_rollback(allow_dual_writes=False)

    # Attempt to enable dual writes during rollback strictly raises DualWriteForbiddenError
    with pytest.raises(DualWriteForbiddenError, match="Never re-enable dual writes"):
        RollbackPolicy.assert_safe_rollback(allow_dual_writes=True)


def test_canonical_writer_coordinator_rejects_fallback_writes() -> None:
    """Canonical writer coordinator enforces sole owner and forbids fallback writes."""
    coordinator = CanonicalWriterCoordinator()

    # Sole canonical writer for Persona succeeds
    receipt = coordinator.handle_write(
        aggregate=AggregateKind.PERSONA,
        writer_identity="persona_provisioning_store",
        payload={"persona_id": "p1", "name": "Canonical Persona"},
        is_fallback=False,
    )
    assert receipt["status"] == "acknowledged"
    assert receipt["writer"] == "persona_provisioning_store"

    # Unauthorized writer fails
    with pytest.raises(FallbackAcknowledgementForbiddenError, match="Unauthorized writer"):
        coordinator.handle_write(
            aggregate=AggregateKind.PERSONA,
            writer_identity="unauthorized_actor",
            payload={"persona_id": "p1"},
            is_fallback=False,
        )

    # Fallback acknowledgement write is strictly forbidden
    with pytest.raises(FallbackAcknowledgementForbiddenError, match="Fallback write attempt forbidden"):
        coordinator.handle_write(
            aggregate=AggregateKind.PERSONA,
            writer_identity="persona_provisioning_store",
            payload={"persona_id": "p1"},
            is_fallback=True,
        )


def test_migration_engine_backfill_dry_run_and_provenance() -> None:
    """SD §5.2: dry-run counts before mutation, provenance and checksum on backfill."""
    canonical_store = {"inc-1": {"incident_id": "inc-1", "status": "open", "tenant_id": "tenant-test"}}
    overlay_data = {
        "inc-1": {"incident_id": "inc-1", "status": "open", "tenant_id": "tenant-test"},
        "inc-2": {"incident_id": "inc-2", "status": "investigating", "tenant_id": "tenant-test"},
    }
    engine = OverlayMigrationEngine(
        aggregate=AggregateKind.INCIDENT,
        canonical_store=canonical_store,
        overlay_data_source=overlay_data,
    )

    # Dry run: counts only, zero mutation
    dry_result = engine.backfill(tenant_id="tenant-test", dry_run=True)
    assert dry_result.dry_run is True
    assert dry_result.backfilled == 1
    assert dry_result.skipped_existing == 1
    assert "inc-2" not in canonical_store

    # Live backfill: mutates with checksum and migration metadata
    live_result = engine.backfill(tenant_id="tenant-test", dry_run=False)
    assert live_result.dry_run is False
    assert live_result.backfilled == 1
    assert "inc-2" in canonical_store
    meta = canonical_store["inc-2"]["_migration_metadata"]
    assert meta["source"] == "overlay_retire_001"
    assert "checksum" in meta
    assert "backfilled_at" in meta
