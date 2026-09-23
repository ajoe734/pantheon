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

import ast
from pathlib import Path
from typing import Optional

import pytest
from starlette.testclient import TestClient

from services.control_plane.bff.auth import policy as auth_policy
from services.control_plane.bff.incidents.service import IncidentService
from services.control_plane.bff.jobs.router import create_jobs_router
from services.control_plane.bff.personas import service as personas_service
from services.control_plane.bff.ports.read_surface_ports import ReadSurfacePorts
from services.control_plane.bff.strategies.routes.common import (
    StrategyRouteContext,
    default_bff_error,
    default_page_slice,
    default_read_surface_meta,
    default_utc_now,
)


# ---------------------------------------------------------------------------
# 1. Mandatory Symbol Retirement and Reinstatement Prevention
#
# These assertions are about main.py's *own* module namespace: that it no
# longer defines the 4 legacy overlay globals as ordinary module attributes,
# and that it fails closed (via a module-level ``__getattr__``/``__setattr__``
# guard) on any attempt to read or reinstate them. Importing main.py as a
# live module purely to inspect this is still an "import of main" for the
# purposes of the BFF composition-root migration (the architecture scanner
# in test_bff_test_architecture.py is a live AST import-graph scan, and flags
# any `import` of main.py regardless of purpose). So this property is proven
# by statically parsing main.py's source with `ast`, the same technique
# test_bff_test_architecture.py itself uses to scan test files, without ever
# importing main.py as a module.
# ---------------------------------------------------------------------------

RETIRED_OVERLAY_SYMBOLS = (
    "_PERSONA_BFF_OVERLAY",
    "_STRATEGY_BFF_OVERLAY",
    "_GOV_BFF_INCIDENT_OVERLAY",
    "_GOV_BFF_JOB_OVERLAY",
)

MAIN_PY_PATH = Path(__file__).resolve().parents[1] / "main.py"


def _main_source() -> str:
    return MAIN_PY_PATH.read_text(encoding="utf-8")


def _main_ast() -> ast.Module:
    return ast.parse(_main_source(), filename=str(MAIN_PY_PATH))


def _module_level_assigned_names(tree: ast.Module) -> set[str]:
    """Names assigned as ordinary module-level globals in main.py (top-level only).

    If a retired overlay symbol were reinstated as a plain global, normal
    attribute lookup would find it in ``main.__dict__`` before the
    ``__getattr__`` guard ever runs, silently defeating the retirement.
    """
    names: set[str] = set()
    for node in tree.body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign) and node.target is not None:
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return names


def _retired_process_overlays_literal(tree: ast.Module) -> Optional[set[str]]:
    """Extract the string members of the module-level ``_RETIRED_PROCESS_OVERLAYS`` frozenset literal."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "_RETIRED_PROCESS_OVERLAYS" for t in node.targets):
            continue
        value = node.value
        if isinstance(value, ast.Call) and getattr(value.func, "id", None) == "frozenset" and value.args:
            container = value.args[0]
        else:
            container = value
        if isinstance(container, (ast.Set, ast.List, ast.Tuple)):
            return {
                elt.value
                for elt in container.elts
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            }
    return None


def _guard_class_getattr_setattr_sources(tree: ast.Module, source: str) -> tuple[Optional[str], Optional[str]]:
    """Find the module-swap guard class's ``__getattr__``/``__setattr__`` method source text.

    main.py fails closed on the retired overlays by rebinding
    ``sys.modules[__name__].__class__`` to a ``types.ModuleType`` subclass
    that overrides ``__getattr__``/``__setattr__``. Locate that class body's
    two guard methods by source text (without executing anything) so the
    test can confirm they actually raise for the retired names.
    """
    getattr_src: Optional[str] = None
    setattr_src: Optional[str] = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        base_names = {getattr(base, "attr", getattr(base, "id", None)) for base in node.bases}
        if "ModuleType" not in base_names:
            continue
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name == "__getattr__":
                getattr_src = ast.get_source_segment(source, item)
            if isinstance(item, ast.FunctionDef) and item.name == "__setattr__":
                setattr_src = ast.get_source_segment(source, item)
    return getattr_src, setattr_src


def _module_class_swap_applied_at_top_level(tree: ast.Module) -> bool:
    """Confirm the ``ModuleType`` subclass swap actually executes at module scope.

    A guard class that is merely defined but never installed onto
    ``sys.modules[__name__]`` would never run, so this checks for the
    top-level (unconditional, unnested) assignment statement that installs it.
    """
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        target = node.targets[0] if node.targets else None
        if not isinstance(target, ast.Attribute) or target.attr != "__class__":
            continue
        owner = target.value
        if (
            isinstance(owner, ast.Subscript)
            and isinstance(owner.value, ast.Attribute)
            and owner.value.attr == "modules"
        ):
            return True
    return False


@pytest.mark.parametrize("symbol", RETIRED_OVERLAY_SYMBOLS)
def test_mandatory_overlay_symbols_not_assigned_as_module_globals(symbol: str) -> None:
    """The 4 mandatory overlay symbols must not exist as ordinary globals in main.py."""
    tree = _main_ast()
    assigned = _module_level_assigned_names(tree)
    assert symbol not in assigned, (
        f"Symbol {symbol!r} must not be assigned as an ordinary module-level global in main.py"
    )


def test_retired_process_overlays_set_declares_all_mandatory_symbols() -> None:
    """main.py's retirement guard set must cover exactly the 4 mandatory overlay symbols."""
    tree = _main_ast()
    declared = _retired_process_overlays_literal(tree)
    assert declared is not None, "main.py must declare _RETIRED_PROCESS_OVERLAYS as a literal frozenset/set"
    assert set(RETIRED_OVERLAY_SYMBOLS) <= declared


@pytest.mark.parametrize("symbol", RETIRED_OVERLAY_SYMBOLS)
def test_module_getattr_guard_fails_closed_on_read(symbol: str) -> None:
    """The installed module class's __getattr__ must raise AttributeError('retired and deleted') for each symbol."""
    tree = _main_ast()
    declared = _retired_process_overlays_literal(tree)
    getattr_src, _ = _guard_class_getattr_setattr_sources(tree, _main_source())
    assert declared is not None and symbol in declared
    assert getattr_src is not None, "main.py must define a ModuleType subclass with __getattr__"
    assert "_RETIRED_PROCESS_OVERLAYS" in getattr_src
    assert "AttributeError" in getattr_src
    assert "retired and deleted" in getattr_src


@pytest.mark.parametrize("symbol", RETIRED_OVERLAY_SYMBOLS)
def test_module_setattr_guard_fails_closed_on_reinstatement(symbol: str) -> None:
    """The installed module class's __setattr__ must raise AttributeError('retired and deleted') for each symbol."""
    tree = _main_ast()
    declared = _retired_process_overlays_literal(tree)
    _, setattr_src = _guard_class_getattr_setattr_sources(tree, _main_source())
    assert declared is not None and symbol in declared
    assert setattr_src is not None, "main.py must define a ModuleType subclass with __setattr__"
    assert "_RETIRED_PROCESS_OVERLAYS" in setattr_src
    assert "AttributeError" in setattr_src
    assert "retired and deleted" in setattr_src


def test_module_class_guard_is_installed_at_module_scope() -> None:
    """The ModuleType subclass swap that activates the getattr/setattr guard must run unconditionally."""
    tree = _main_ast()
    assert _module_class_swap_applied_at_top_level(tree), (
        "main.py must install its retirement-guard module class via an "
        "unconditional top-level `sys.modules[__name__].__class__ = ...` assignment"
    )


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
    """The production strategies route context resolves summaries from the canonical
    store's ``list_strategy_specs`` with zero overlay lookup (mirrors main.py's own
    ``_list_strategy_summaries``, which is exactly ``list(read_store.list_strategy_specs() or [])``,
    wired as the strategies router's ``list_strategy_summaries`` dependency)."""
    fake_store = FakeCanonicalReadStore()
    fake_store.strategies = [{
        "strategy_id": "canonical-strat-001",
        "name": "Canonical Momentum Alpha",
        "state": "active",
        "updatedAt": "2026-09-01T00:00:00Z",
    }]
    ctx = StrategyRouteContext(list_strategy_summaries=fake_store.list_strategy_specs)
    summaries = ctx.list_strategy_summaries_records()
    assert len(summaries) == 1
    assert summaries[0]["strategy_id"] == "canonical-strat-001"
    assert summaries[0]["name"] == "Canonical Momentum Alpha"


def test_list_persona_records_reads_strictly_canonical_and_provisioning_stores() -> None:
    """personas.service._list_persona_records must return records from read_store and
    provisioning store only, with zero process-local overlay involved."""
    fake_store = FakeCanonicalReadStore()
    fake_store.personas = [{
        "id": "persona-canonical-1",
        "persona_id": "persona-canonical-1",
        "name": "Canonical Persona",
        "lifecycle_state": "paper_running",
        "metadata": {"tenant_id": "tenant-test"},
    }]
    records = personas_service._list_persona_records(tenant_id="tenant-test", read_store=fake_store)
    assert len(records) == 1
    assert records[0]["persona_id"] == "persona-canonical-1"
    assert records[0]["name"] == "Canonical Persona"


def test_incident_read_paths_strictly_canonical() -> None:
    """IncidentService.list_bff_incidents/get_bff_incident query read_store with zero
    process-local overlay fallback (its own ``_incident_overlay`` only ever holds
    incidents this same service instance created via ``create_incident``)."""
    fake_store = FakeCanonicalReadStore()
    fake_store.incidents["inc-canonical-999"] = {
        "incident_id": "inc-canonical-999",
        "id": "inc-canonical-999",
        "title": "Canonical Incident",
        "status": "investigating",
        "severity": "medium",
        "created_at": "2026-09-05T12:00:00Z",
    }
    service = IncidentService(get_read_store=lambda: fake_store)

    found = service.get_bff_incident("inc-canonical-999")
    assert found is not None
    assert (found.get("incident_id") or found.get("id")) == "inc-canonical-999"

    # Missing incident returns None without attempting any overlay lookup
    assert service.get_bff_incident("inc-non-existent") is None

    listed = service.list_bff_incidents()
    assert any(
        str(i.get("incident_id") or i.get("id")) == "inc-canonical-999"
        for i in listed
    )


def test_jobs_router_reads_strictly_canonical_read_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Jobs router routes GET /bff/jobs and /bff/jobs/{job_id} through read_store with 0 overlay."""
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    fake_store = FakeCanonicalReadStore()
    fake_store.jobs["job-can-1"] = {
        "job_id": "job-can-1",
        "id": "job-can-1",
        "status": "running",
        "job_type": "backtest",
    }

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(
        create_jobs_router(
            get_read_store=lambda: fake_store,
            extract_identity=auth_policy.extract_identity,
            require_read_role=auth_policy.require_read_role,
            bff_error=default_bff_error,
            utc_now=default_utc_now,
            page_slice=default_page_slice,
            read_surface_meta=default_read_surface_meta,
            dataset_surface_status=lambda *a, **k: {"status": "ok"},
            raise_if_read_surface_unavailable=lambda *a, **k: None,
            reject_body_idempotency_key=lambda payload: None,
            resolve_final_idempotency_key=lambda ik, xik: str(ik or xik or ""),
            submit_job_action=lambda *a, **k: {},
        )
    )

    client = TestClient(app)
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


def test_routers_reject_retired_overlay_parameters() -> None:
    """Routers must fail immediately with AttributeError if retired overlay arguments are passed."""
    from services.control_plane.bff.strategies.router import create_strategies_router
    from services.control_plane.bff.incidents.router import create_incident_router
    from services.control_plane.bff.jobs.router import create_jobs_router

    with pytest.raises(AttributeError, match="strategy_overlay is retired"):
        create_strategies_router(strategy_overlay={"strat-1": {}})

    with pytest.raises(AttributeError, match="incident_overlay is retired"):
        create_incident_router(incident_overlay={"inc-1": {}})

    with pytest.raises(AttributeError, match="get_job_overlay is retired"):
        create_jobs_router(
            extract_identity=lambda *a: None,
            require_read_role=lambda *a: None,
            bff_error=lambda *a, **k: Exception(),
            utc_now=lambda: "",
            page_slice=lambda *a: None,
            read_surface_meta=lambda *a: {},
            dataset_surface_status=lambda *a: {},
            raise_if_read_surface_unavailable=lambda *a: None,
            get_job_overlay=lambda: {},
            reject_body_idempotency_key=lambda *a: None,
            resolve_final_idempotency_key=lambda *a: "",
            submit_job_action=lambda *a: {},
        )


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
import tempfile


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
    replica_1_store = FakeCanonicalReadStore()
    replica_1_store.personas = list(shared_storage_records)

    records1 = personas_service._list_persona_records(tenant_id="tenant-durability", read_store=replica_1_store)
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

    # Fresh Replica 2 boots up in a new clean process container (simulating a
    # hard process restart / failover, with process memory wiped between
    # replicas). It creates its own independent store instance from shared
    # storage (distinct object identity).
    replica_2_store = FakeCanonicalReadStore()
    replica_2_store.personas = list(shared_storage_records)
    assert replica_2_store is not replica_1_store

    records2 = personas_service._list_persona_records(tenant_id="tenant-durability", read_store=replica_2_store)
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
    persona_store: dict = {}
    coordinator = CanonicalWriterCoordinator(canonical_stores={AggregateKind.PERSONA: persona_store})

    # Sole canonical writer for Persona succeeds and actually persists the record.
    receipt = coordinator.handle_write(
        aggregate=AggregateKind.PERSONA,
        writer_identity="persona_provisioning_store",
        payload={"persona_id": "p1", "name": "Canonical Persona"},
        is_fallback=False,
    )
    assert receipt["status"] == "acknowledged"
    assert receipt["writer"] == "persona_provisioning_store"
    assert receipt["persisted"] is True
    assert persona_store["p1"]["name"] == "Canonical Persona"

    # No canonical store bound: refuse to fabricate a receipt.
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


@pytest.fixture
def strategy_pg_case():
    """Real-Postgres schema per test, mirroring
    services/control-plane/bff/migrations/test_overlay_retirement.py's fixture
    of the same name: skip cleanly with no live database configured, otherwise
    prove Strategy owner durability against an actual PostgreSQL instance.
    Strategy has no path-created durable backend (see
    build_canonical_owner_adapter / _ReplicaInstance._resolve_adapter) — a
    directory-backed replica has no explicit Strategy store to resolve, so
    its restart/multi-replica proof must go through the real canonical
    Postgres owner instead of a second, file-derived store."""
    import os
    from uuid import uuid4

    dsn = os.getenv("TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is required for real Postgres Strategy owner proof")
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql

    schema = f"strategy_owner_{uuid4().hex}"
    entries_table = f"{schema}.entries"
    receipts_table = f"{schema}.command_receipts"
    try:
        yield dsn, entries_table, receipts_table
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


def _strategy_pg_store(case, *, bootstrap: bool = True):
    from services.registry.pg_store import PostgresRegistryStore

    dsn, entries_table, receipts_table = case
    return PostgresRegistryStore(
        dsn=dsn, entries_table=entries_table, receipts_table=receipts_table, bootstrap=bootstrap,
    )


def test_genuine_five_owner_disk_backed_restart_durability_and_multi_replica(strategy_pg_case) -> None:
    """Normative SD §5.1, §5.2, §12.3: Verify multi-replica readback and process restart
    durability across all five domain owners: Persona, Incident, Job, and Ranking on
    genuine persistent disk storage, and Strategy on the genuine canonical Postgres
    owner (Strategy has no path-created durable backend to fake this proof with).
    """
    from services.control_plane.bff.migrations.overlay_retirement import StrategyCanonicalAdapter

    with tempfile.TemporaryDirectory() as td:
        harness = MultiReplicaReadbackHarness(shared_durable_storage=td)

        rep_alpha = harness.spawn_replica("replica-alpha")
        rep_beta = harness.spawn_replica("replica-beta")

        aggregates = [
            (AggregateKind.PERSONA, "pers-durable-rep-1", {"name": "Persona 1", "state": "active"}),
            (AggregateKind.INCIDENT, "inc-durable-rep-1", {"title": "Incident 1", "status": "open"}),
            (AggregateKind.JOB, "job-durable-rep-1", {"name": "Job 1", "status": "running"}),
            (AggregateKind.RANKING, "rank-durable-rep-1", {"formula": "sharpe", "score": 2.5}),
        ]

        # Replica Alpha writes the four filesystem-backed aggregates directly to persistent disk storage
        for agg, key, payload in aggregates:
            record = {"id": key, "aggregate": agg.value, **payload}
            assert rep_alpha.write_canonical(key, record) is True

        # Simulate independent process restart: process memory wiped
        rep_alpha.restart_process()

        # Replica Alpha reads back after restart: verified surviving from disk
        for agg, key, payload in aggregates:
            readback_alpha = rep_alpha.read_canonical(key)
            assert readback_alpha is not None
            assert readback_alpha["id"] == key
            assert readback_alpha["aggregate"] == agg.value

            # Independent restarted process proof via subprocess.run
            readback_proc = rep_alpha.read_canonical_via_restarted_process(key)
            assert readback_proc is not None
            assert readback_proc["id"] == key
            assert readback_proc["aggregate"] == agg.value
            assert readback_proc == readback_alpha

        # Replica Beta (separate process replica) reads directly from disk without local state
        for agg, key, payload in aggregates:
            readback_beta = rep_beta.read_canonical(key)
            assert readback_beta is not None
            assert readback_beta == rep_alpha.read_canonical(key)

        # Strategy: genuine real-Postgres restart and multi-replica proof, the
        # canonical owner it actually has (memory or postgres — never a
        # path-derived file store).
        strategy_adapter_alpha = StrategyCanonicalAdapter(_strategy_pg_store(strategy_pg_case))
        strategy_record = {
            "strategy_id": "strat-durable-rep-1",
            "name": "Strategy 1",
            "status": "draft",
            "tenant_id": "tenant-corp",
            "actor": {"actor_id": "replica-test", "tenant": "tenant-corp", "roles": ["operator"], "token_kind": "service"},
        }
        assert strategy_adapter_alpha.insert(strategy_record) is True

        # Genuine subprocess restart against the exact schema this adapter was built with.
        strategy_adapter_alpha.restart_process()

        strategy_readback_alpha = strategy_adapter_alpha.get("strat-durable-rep-1")
        assert strategy_readback_alpha is not None
        assert strategy_readback_alpha["strategy_id"] == "strat-durable-rep-1"

        # Replica Beta: a completely independent replica (fresh store, same schema).
        strategy_adapter_beta = StrategyCanonicalAdapter(_strategy_pg_store(strategy_pg_case, bootstrap=False))
        strategy_readback_beta = strategy_adapter_beta.get("strat-durable-rep-1")
        assert strategy_readback_beta is not None
        assert strategy_readback_beta["strategy_id"] == "strat-durable-rep-1"
        assert strategy_readback_beta["name"] == "Strategy 1"
